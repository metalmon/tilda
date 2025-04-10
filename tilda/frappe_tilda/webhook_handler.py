# Copyright (c) 2024, Bucher Industries AG and contributors
# For license information, please see license.txt

import frappe
import json
from frappe.utils import now_datetime, cint, flt, get_datetime, safe_decode, escape_html, call
from frappe.model.document import Document
from urllib.parse import unquote_plus, parse_qs, unquote, urljoin
import time
import traceback

# --- API Endpoint ---

@frappe.whitelist(allow_guest=True)
def handle_webhook():
    """
    API endpoint to receive webhook POST requests from Tilda (expects application/json).
    Validates the request, optionally logs it, and enqueues a background job.
    URL: /api/method/tilda.frappe_tilda.webhook_handler.handle_webhook?config={config_name}&key={secret_key}
    Method: POST
    Payload: application/json
    """
    # --- Get parameters and add diagnostic logging at the very start ---
    # Get config_name and key from request query parameters directly first
    config_name = frappe.request.args.get("config")
    request_key = frappe.request.args.get("key")
    config_name_for_log = config_name or "UNKNOWN_CONFIG"

    # print(f"--- Tilda Webhook: ENTERING handle_webhook for config '{config_name_for_log}' ---")
    try:
        frappe.logger().info(f"--- Tilda Webhook: ENTERING handle_webhook for config '{config_name_for_log}' via logger ---")
    except Exception as log_init_err:
        # print(f"--- Tilda Webhook: ERROR initializing logger: {log_init_err} ---")
        pass # Avoid stopping execution if logger fails initially
    # -------------------------------------------------------------------

    received_at = now_datetime()
    # config_name and request_key are already retrieved above
    # config_name = frappe.request.args.get("config")
    # request_key = frappe.request.args.get("key")

    # Update diagnostic logging variable - no longer needed here as it's set above
    # config_name_for_log = config_name or "UNKNOWN_CONFIG"

    log_name = None
    decoded_data = {}

    try:
        # 1. Check if config_name and request_key are provided
        if not config_name:
            frappe.throw("Webhook Configuration Name 'config' not specified in query parameters.")
        if not request_key:
            frappe.throw("Secret Key 'key' not provided in query parameters.")

        # 2. Get Webhook Configuration document
        try:
            config = frappe.get_doc("Tilda Webhook Configuration", config_name)
        except frappe.DoesNotExistError:
            frappe.throw(f"Webhook Configuration '{config_name}' not found.")

        # 3. Validate Secret Key
        # Use frappe.utils.password.check_password for constant-time comparison
        # Use get_password() method for password fields
        stored_key = config.get_password('secret_key')
        # --- Add diagnostic logging for keys ---
        # print(f"--- Tilda Webhook DEBUG: Stored Key = '{stored_key}' (Type: {type(stored_key)}) ---")
        # print(f"--- Tilda Webhook DEBUG: Request Key = '{request_key}' (Type: {type(request_key)}) ---")
        # ---------------------------------------
        # Replace check_password with direct string comparison for debugging
        # if not stored_key or not frappe.utils.password.check_password(stored_key, request_key):
        keys_match = stored_key == request_key
        # print(f"--- Tilda Webhook DEBUG: Direct comparison result (stored == request): {keys_match} ---")
        if not stored_key or not keys_match:
            # Log the failure reason
            reason = "Stored key missing" if not stored_key else "Direct comparison failed"
            # print(f"--- Tilda Webhook DEBUG: Key check failed. Reason: {reason} ---")
            frappe.throw("Invalid Secret Key.")

        # 4. Check if enabled
        if not config.enabled:
            # Optionally log disabled access attempts if desired
            frappe.throw(f"Webhook Configuration '{config_name}' is disabled.")

        # 5. Parse JSON from request body
        try:
            raw_data = frappe.request.data
            if not raw_data:
                decoded_data = {}
            else:
                decoded_data = frappe.parse_json(frappe.safe_decode(raw_data))
            if not isinstance(decoded_data, dict):
                frappe.throw("Invalid JSON payload: Expected a JSON object.")

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            frappe.throw(f"Invalid JSON payload: {e}")

        # 5.1 Handle Tilda's test=test ping (Tilda может не слать test ping для JSON, но оставим на всякий случай)
        if len(decoded_data) == 1 and decoded_data.get("test") == "test":
            frappe.local.response["http_status_code"] = 200
            return {"status": "success", "message": "Webhook test connection successful."}

        # 6. Optional: Create Initial Log Entry
        if config.enable_logging:
            try:
                log_doc = frappe.new_doc("Tilda Webhook Log")
                log_doc.webhook_configuration = config.name
                log_doc.received_at = received_at
                log_doc.status = "Processing"
                log_doc.payload = json.dumps(decoded_data, indent=2, ensure_ascii=False)
                log_doc.insert(ignore_permissions=True)
                log_name = log_doc.name
                frappe.db.commit()
            except Exception as e:
                # Log the logging error itself, but don't stop processing
                frappe.log_error(f"Failed to create initial webhook log for {config.name}: {e}", "Tilda Webhook Logging Error")
                log_name = None # Ensure we don't try to update a non-existent log

        # 7. Enqueue Background Job
        frappe.enqueue(
            "tilda.frappe_tilda.webhook_handler.process_webhook_data",
            queue="short", # Or 'default' / 'long' depending on expected processing time
            timeout=300, # 5 minutes timeout
            config_name=config.name,
            payload=decoded_data,
            log_name=log_name # Pass log name if created
        )

        # 8. Return Success Response to Tilda
        frappe.local.response["http_status_code"] = 200
        # Tilda expects 'ok' or just 200 status based on some examples, let's return a simple JSON
        return {"status": "success", "message": "Webhook data received and queued for processing."}

    except frappe.ValidationError as e:
        frappe.log_error(message=f"Webhook Validation Error ({config_name}): {e}", title="Tilda Webhook Error")
        frappe.local.response["http_status_code"] = getattr(e, "http_status_code", 400)
        return {"status": "error", "message": str(e)}
    except Exception as e:
        # Catch any other unexpected errors during initial handling
        # --- Add diagnostic logging before logging the error ---
        # print(f"--- Tilda Webhook: CAUGHT UNHANDLED EXCEPTION for config '{config_name_for_log}': {e} ---")
        try:
            frappe.logger().error(f"--- Tilda Webhook: CAUGHT UNHANDLED EXCEPTION for config '{config_name_for_log}' via logger: {e} ---")
        except Exception as log_err_err:
            # print(f"--- Tilda Webhook: ERROR using logger in exception handler: {log_err_err} ---")
            pass # Avoid stopping execution if logger fails in handler
        # ---------------------------------------------------
        frappe.log_error(message=traceback.format_exc(), title="Tilda Webhook Unhandled Error")
        frappe.local.response["http_status_code"] = 500
        # Optionally update log status to Error here if log_name exists
        if log_name:
            try:
                log_doc = frappe.get_doc("Tilda Webhook Log", log_name)
                log_doc.status = "Error"
                log_doc.message = f"Error during initial handling: {traceback.format_exc()}"
                log_doc.processing_time_ms = round((time.time() - received_at) * 1000)
                log_doc.save(ignore_permissions=True)
                frappe.db.commit()
            except Exception as log_e:
                frappe.log_error(f"Failed to update log status after initial error for {log_name}: {log_e}", "Tilda Webhook Logging Error")

        return {"status": "error", "message": "Internal Server Error during webhook handling."}


# --- Background Job ---

def parse_tilda_cookies(cookie_string: str) -> dict:
    """
    Parses the COOKIES string from Tilda into a dictionary of key-value pairs.
    Format: key1=value1; key2=value2; ...
    Keys and values are URL-decoded.
    """
    cookies = {}
    if not cookie_string:
        return cookies

    # Split into individual cookies
    pairs = cookie_string.split(';')
    for pair in pairs:
        pair = pair.strip()
        if '=' in pair:
            # Split only on the first equals sign
            key, value = pair.split('=', 1)
            try:
                # Decode key and value (Tilda might encode them)
                decoded_key = unquote_plus(key.strip())
                decoded_value = unquote_plus(value.strip())
                cookies[decoded_key] = decoded_value
            except Exception as e:
                # Log error but continue parsing other cookies
                frappe.log_error(f"Error decoding cookie pair '{pair}': {e}", "Tilda Cookie Parsing Error")

    return cookies

def process_webhook_data(config_name: str, payload: dict, log_name: str = None):
    """
    Background job to process the received Tilda data and create a Frappe document.
    """
    frappe.logger().info(f"[Tilda Process {config_name}] Starting job for log '{log_name}'. Payload keys: {list(payload.keys()) if payload else 'None'}")
    start_time = time.time()
    log_doc = None
    if log_name:
        try:
            log_doc = frappe.get_doc("Tilda Webhook Log", log_name)
        except Exception as e:
            frappe.log_error(f"Failed to retrieve log doc {log_name} for processing: {e}", "Tilda Webhook Processing Error")
            log_name = None

    try:
        # Use get_doc instead of get_cached_doc to avoid stale configuration
        # config = frappe.get_cached_doc("Tilda Webhook Configuration", config_name)
        config = frappe.get_doc("Tilda Webhook Configuration", config_name)
        target_doctype = config.target_doctype
        mappings = config.field_mappings
        default_values = config.default_values

        if not target_doctype:
            raise ValueError("Target Doctype is not configured.")
        # Allow processing even without field mappings (e.g., only defaults or cookies)
        # if not mappings:
        #     frappe.logger().warning(f"[Tilda Process {config_name}] No field mappings configured.")

        new_doc_data = {"doctype": target_doctype}
        processed_tilda_fields = set() # Keep track of fields processed from payload or cookies

        # Get target doctype metadata
        target_meta = frappe.get_meta(target_doctype)

        # --- Helper Function for Data Conversion ---
        def convert_value(value, field_meta):
            if value is None or value == '':
                return None
            try:
                if field_meta.fieldtype in ["Int", "Check"]:
                    return cint(value)
                elif field_meta.fieldtype in ["Float", "Currency", "Percent"]:
                    return flt(value)
                elif field_meta.fieldtype in ["Date"]:
                    try: return get_datetime(value).date()
                    except Exception: return None
                elif field_meta.fieldtype in ["Datetime"]:
                    try: return get_datetime(value)
                    except Exception: return None
                else: # Data, Text, Link, etc.
                    return value
            except Exception as conversion_error:
                frappe.log_error(f"Error converting value '{value}' for field '{field_meta.fieldname}' (Type: {field_meta.fieldtype}): {conversion_error}", "Tilda Webhook Data Conversion Error")
                return None # Return None if conversion fails
        # -------------------------------------------

        # 1. Process COOKIES (Universal Parsing)
        if "COOKIES" in payload:
            cookie_data = parse_tilda_cookies(payload["COOKIES"])
            frappe.logger().info(f"[Tilda Process {config_name}] Parsed Cookies: {cookie_data}") # Log parsed cookies

            for cookie_key, cookie_value in cookie_data.items():
                mapped_to_frappe = None
                if mappings:
                    for mapping in mappings:
                        if mapping.tilda_field_name == cookie_key:
                            mapped_to_frappe = mapping.frappe_field_name
                            break

                if mapped_to_frappe:
                    frappe_field = mapped_to_frappe
                    field_meta = target_meta.get_field(frappe_field)
                    if field_meta:
                        converted = convert_value(cookie_value, field_meta)
                        if converted is not None or (cookie_value is None or cookie_value == ''):
                            new_doc_data[frappe_field] = converted
                            processed_tilda_fields.add(cookie_key) # Mark as processed
                            frappe.logger().info(f"[Tilda Process {config_name}] Mapped Cookie: '{cookie_key}' -> '{frappe_field}' = {converted}")
                    else:
                        frappe.log_error(f"Mapped Frappe field '{frappe_field}' (from cookie '{cookie_key}') not found in Doctype '{target_doctype}'.", "Tilda Webhook Configuration Error")
                # else: # Optional: Log unmapped cookies if needed
                #     frappe.logger().debug(f"[Tilda Process {config_name}] Unmapped cookie: {cookie_key}")
            processed_tilda_fields.add("COOKIES") # Mark COOKIES key itself as processed

        # 2. Process other payload fields using mappings
        for tilda_field, value in payload.items():
            # Skip if already processed (e.g., was a mapped cookie key or the COOKIES key itself)
            if tilda_field in processed_tilda_fields:
                continue

            # Skip internal Tilda fields unless explicitly mapped
            # Allow mapping them if needed, so remove this check or make it conditional
            # if tilda_field in ["tranid", "formid", "formname", "pageid", "formland", "userid", "date", "time", "ip", "useragent", "referrer"]:
            #     # Check if it's EXPLICITLY mapped
            #     is_explicitly_mapped = False
            #     if mappings:
            #         for mapping in mappings:
            #             if mapping.tilda_field_name == tilda_field:
            #                 is_explicitly_mapped = True
            #                 break
            #     if not is_explicitly_mapped:
            #         continue # Skip if it's an internal field and not explicitly mapped

            mapped_to_frappe = None
            if mappings:
                for mapping in mappings:
                    if mapping.tilda_field_name == tilda_field:
                        mapped_to_frappe = mapping.frappe_field_name
                        break

            if mapped_to_frappe:
                frappe_field = mapped_to_frappe
                field_meta = target_meta.get_field(frappe_field)
                if field_meta:
                    # Avoid overwriting data already set (e.g., by a cookie mapping for the same tilda_field_name)
                    if frappe_field in new_doc_data:
                         frappe.logger().warning(f"[Tilda Process {config_name}] Field '{frappe_field}' already set (possibly by cookie '{tilda_field}'). Skipping mapping from payload field '{tilda_field}'.")
                         continue

                    converted = convert_value(value, field_meta)
                    if converted is not None or (value is None or value == ''):
                         new_doc_data[frappe_field] = converted
                         frappe.logger().info(f"[Tilda Process {config_name}] Mapped Payload: '{tilda_field}' -> '{frappe_field}' = {converted}")
                    else:
                        frappe.log_error(f"Mapped Frappe field '{frappe_field}' (from payload field '{tilda_field}') not found in Doctype '{target_doctype}'.", "Tilda Webhook Configuration Error")
            # else: # Optional: Log unmapped payload fields if needed
            #     frappe.logger().debug(f"[Tilda Process {config_name}] Unmapped payload field: {tilda_field}")

        # 3. Apply default values
        frappe.logger().info(f"[Tilda Process {config_name}] Applying default values...")
        if default_values:
            for default_entry in default_values:
                frappe_field = default_entry.frappe_field_name
                default_text_value = default_entry.default_value
                overwrite = default_entry.overwrite_if_exists

                if overwrite or frappe_field not in new_doc_data:
                    field_meta = target_meta.get_field(frappe_field)
                    if field_meta:
                        converted = convert_value(default_text_value, field_meta)
                        if converted is not None or (default_text_value is None or default_text_value == ''):
                            new_doc_data[frappe_field] = converted
                            frappe.logger().info(f"[Tilda Process {config_name}] Applied Default: '{frappe_field}' = {converted}")
                    else:
                        frappe.log_error(f"Default value Frappe field '{frappe_field}' not found in Doctype '{target_doctype}'.", "Tilda Webhook Configuration Error")

        # 4. Create the new Frappe document
        frappe.logger().info(f"[Tilda Process {config_name}] Final data before creating doc: {new_doc_data}")
        if len(new_doc_data) > 1: # Ensure we have more than just {"doctype": ...}
            new_doc = frappe.get_doc(new_doc_data)
            # Set naming series if applicable
            if target_meta.autoname == "naming_series":
                 new_doc.set_naming_series()
            elif target_meta.autoname and target_meta.autoname.startswith("field:"):
                field_name = target_meta.autoname.split(":")[1]
                if not new_doc.get(field_name):
                    # Maybe generate a name or throw error if naming field is empty
                    pass # Let Frappe handle naming error or implement custom logic

            new_doc.insert(ignore_permissions=True) # Assuming guest access means we ignore permissions here
            frappe.db.commit()
            success_message = f"Successfully created {target_doctype} {new_doc.name}."
            if log_doc:
                log_doc.status = "Success"
                log_doc.message = success_message
                log_doc.target_document_type = target_doctype
                log_doc.target_document_name = new_doc.name
                log_doc.processing_time_ms = round((time.time() - start_time) * 1000)
                log_doc.save(ignore_permissions=True)
                frappe.db.commit()
            frappe.logger().info(f"[Tilda Process {config_name}] {success_message}")
        else:
            no_data_message = "No data mapped or defaulted to create the document."
            frappe.logger().warning(f"[Tilda Process {config_name}] {no_data_message}") # Log as warning
            if log_doc:
                log_doc.status = "Error"
                log_doc.message = no_data_message
                log_doc.processing_time_ms = round((time.time() - start_time) * 1000)
                log_doc.save(ignore_permissions=True)
                frappe.db.commit()
            raise ValueError(no_data_message)

    except Exception as e:
        error_message = f"Error processing webhook data: {traceback.format_exc()}"
        frappe.log_error(error_message, f"Tilda Webhook Processing Error ({config_name})")
        if log_doc:
            try:
                log_doc.status = "Error"
                log_doc.message = error_message
                log_doc.processing_time_ms = round((time.time() - start_time) * 1000)
                log_doc.save(ignore_permissions=True)
                frappe.db.commit()
            except Exception as log_e:
                frappe.log_error(f"Failed to update log status after processing error for {log_name}: {log_e}", "Tilda Webhook Logging Error") 