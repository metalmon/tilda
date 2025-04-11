# Copyright (c) 2024, Bucher Industries AG and contributors
# For license information, please see license.txt

import frappe
import json
from frappe.utils import now_datetime, cint, flt, get_datetime, safe_decode, escape_html, call
from frappe.model.document import Document
from urllib.parse import unquote_plus, parse_qs, unquote, urljoin
import time
import traceback
from tilda.frappe_tilda.utils import apply_mappings_and_defaults

# --- API Endpoint ---

@frappe.whitelist(allow_guest=True)
def handle_webhook():
    """
    API endpoint to receive webhook POST requests from Tilda (expects application/json).
    Validates the request, optionally logs it, and enqueues a background job.
    URL: /api/method/tilda.frappe_tilda.webhook_handler.handle_webhook?key={secret_key}
    Method: POST
    Payload: application/json
    """
    # --- Get parameters and add diagnostic logging at the very start ---
    # Get key from request query parameters
    request_key = frappe.request.args.get("key")
    # Initialize config as None - we'll set it when found
    config = None

    # print(f"--- Tilda Webhook: ENTERING handle_webhook for config '{config_name_for_log}' ---")
    try:
        frappe.logger().info(f"--- Tilda Webhook: ENTERING handle_webhook with provided key ---")
    except Exception as log_init_err:
        # print(f"--- Tilda Webhook: ERROR initializing logger: {log_init_err} ---")
        pass # Avoid stopping execution if logger fails initially
    # -------------------------------------------------------------------

    received_at = now_datetime()
    log_name = None
    decoded_data = {}

    try:
        # 1. Check if request_key is provided
        if not request_key:
            frappe.throw("Secret Key 'key' not provided in query parameters.")
            
        # 2. Find webhook configuration by key - direct db query
        # Get the configuration document directly in a single query
        # We can use db_get to directly get the first matching document
        try:
            config = frappe.get_doc("Tilda Webhook Configuration", 
                                   {"secret_key": request_key, "enabled": 1})
            
            frappe.logger().info(f"--- Tilda Webhook: Found matching configuration: '{config.name}' ---")
        except frappe.DoesNotExistError:
            frappe.throw("Invalid Secret Key. No matching configuration found.")

        # 3. Parse JSON from request body
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

        # 3.1 Handle Tilda's test=test ping (Tilda может не слать test ping для JSON, но оставим на всякий случай)
        if len(decoded_data) == 1 and decoded_data.get("test") == "test":
            frappe.local.response["http_status_code"] = 200
            return {"status": "success", "message": "Webhook test connection successful."}

        # 4. Optional: Create Initial Log Entry
        if config.enable_logging:
            try:
                log_doc = frappe.get_doc({
                    "doctype": "Tilda Webhook Log",
                    "webhook_configuration": config.name,
                    "received_at": received_at,
                    "status": "Processing",
                    "payload": json.dumps(decoded_data, indent=2, ensure_ascii=False)
                })
                log_doc.insert(ignore_permissions=True)
                log_name = log_doc.name
                frappe.db.commit()
            except Exception as e:
                # Log the logging error itself, but don't stop processing
                frappe.log_error(f"Failed to create initial webhook log for {config.name}: {e}", "Tilda Webhook Logging Error")
                log_name = None # Ensure we don't try to update a non-existent log

        # 5. Enqueue Background Job
        frappe.enqueue(
            "tilda.frappe_tilda.webhook_handler.process_webhook_data",
            queue="short", # Or 'default' / 'long' depending on expected processing time
            timeout=300, # 5 minutes timeout
            config_name=config.name,
            payload=decoded_data,
            log_name=log_name # Pass log name if created
        )

        # 6. Return Success Response to Tilda
        frappe.local.response["http_status_code"] = 200
        # Tilda expects 'ok' or just 200 status based on some examples, let's return a simple JSON
        return {"status": "success", "message": "Webhook data received and queued for processing."}

    except frappe.ValidationError as e:
        frappe.log_error(message=f"Webhook Validation Error ({config.name if config else 'UNKNOWN_CONFIG'}): {e}", title="Tilda Webhook Error")
        frappe.local.response["http_status_code"] = getattr(e, "http_status_code", 400)
        return {"status": "error", "message": str(e)}
    except Exception as e:
        # Catch any other unexpected errors during initial handling
        # --- Add diagnostic logging before logging the error ---
        # print(f"--- Tilda Webhook: CAUGHT UNHANDLED EXCEPTION for config '{config_name_for_log}': {e} ---")
        try:
            frappe.logger().error(f"--- Tilda Webhook: CAUGHT UNHANDLED EXCEPTION for config '{config.name if config else 'UNKNOWN_CONFIG'}' via logger: {e} ---")
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
        processed_tilda_fields = set() # Keep track of specific Tilda fields processed

        # Get target doctype metadata
        target_meta = frappe.get_meta(target_doctype)

        # --- Helper Function for Data Conversion --- REMOVED in favor of moving to utils or being handled within apply_mappings
        # [ ... removed convert_value function ... ]
        # -------------------------------------------

        # --- Apply Mappings and Defaults using the utility function ---
        cookies_string = payload.pop('COOKIES', '') # Extract cookies string, remove from main payload
        # Ensure payload is a dictionary if it came in as JSON string
        # Note: The handle_webhook function should already provide payload as dict
        payload_dict = payload if isinstance(payload, dict) else {}

        new_doc_data = apply_mappings_and_defaults(
            target_doctype=target_doctype,
            payload=payload_dict,
            cookies_string=cookies_string, # Pass raw string
            field_mappings=config.get("field_mappings", []),
            default_values=config.get("default_values", [])
        )

        # --- Remove the old mapping/defaults logic ---
        # [ The large block of code handling cookie parsing, mappings (Overwrite/Concatenate), and defaults is replaced by the call above ]
        # --- End Removal ---

        # --- Existing logging and validation logic ---
        # Log the final data dictionary just before attempting to insert
        # Use INFO level, but keep print for debugging if needed
        log_msg_data = f"[Tilda Process {config_name}] Final data after apply_mappings_and_defaults: {new_doc_data}"
        frappe.logger().info(log_msg_data)
        # print(log_msg_data)

        # --- Remove the incorrect CRM Lead specific validation block ---
        # [ The if target_doctype == "CRM Lead": ... block is removed here ]
        # --- End Removal ---

        # 4. Create the new Frappe document
        # Log data AGAIN right before get_doc, this time with ERROR level for visibility
        log_msg_final_data = f"[Tilda Process {config_name}] Data just before get_doc: {new_doc_data}"
        frappe.logger().error(log_msg_final_data)
        print(log_msg_final_data) # Add print statement

        if len(new_doc_data) > 1: # Ensure we have more than just {"doctype": ...}
            new_doc = frappe.get_doc(new_doc_data)
            # Add log after get_doc to confirm success
            log_msg_get_doc = f"[Tilda Process {config_name}] Successfully got doc object for {new_doc.name if new_doc else 'None'}"
            frappe.logger().error(log_msg_get_doc)
            print(log_msg_get_doc) # Add print statement
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
            return new_doc.name # Return the name of the created document
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
        # Add print statement for the caught exception
        print(f"--- [Tilda Process {config_name}] EXCEPTION CAUGHT --- ")
        print(error_message)
        print(f"--- END EXCEPTION --- ")
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