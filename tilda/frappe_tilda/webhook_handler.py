# Copyright (c) 2024, Bucher Industries AG and contributors
# For license information, please see license.txt

import frappe
import json
from frappe.utils import now_datetime, get_request_form_data, cint, flt, get_datetime
from frappe.model.document import Document
from urllib.parse import unquote_plus
import time
import traceback

# --- API Endpoint ---

@frappe.whitelist(allow_guest=True)
def handle_webhook(config_name: str):
    """
    API endpoint to receive webhook POST requests from Tilda.
    Validates the request, optionally logs it, and enqueues a background job.
    URL: /api/method/tilda.frappe_tilda.webhook_handler.handle_webhook/{config_name}?key={secret_key}
    Method: POST
    Payload: application/x-www-form-urlencoded
    """
    received_at = now_datetime()
    request_key = frappe.local.form_dict.get("key")
    request_data = get_request_form_data() # Gets merged GET and POST data

    # Clean up data - remove the key from the payload dict
    if "key" in request_data:
        del request_data["key"]

    log_name = None

    try:
        # 1. Check if config_name and request_key are provided
        if not config_name:
            frappe.throw("Webhook Configuration Name not specified in URL.", http_status_code=400)
        if not request_key:
            frappe.throw("Secret Key 'key' not provided in query parameters.", http_status_code=400)

        # 2. Get Webhook Configuration document
        try:
            config = frappe.get_doc("Tilda Webhook Configuration", config_name)
        except frappe.DoesNotExistError:
            frappe.throw(f"Webhook Configuration '{config_name}' not found.", http_status_code=404)

        # 3. Validate Secret Key
        # Use frappe.utils.password.check_password for constant-time comparison
        if not frappe.utils.password.check_password(config.secret_key, request_key):
            frappe.throw("Invalid Secret Key.", http_status_code=403)

        # 4. Check if enabled
        if not config.enabled:
            # Optionally log disabled access attempts if desired
            frappe.throw(f"Webhook Configuration '{config_name}' is disabled.", http_status_code=403)

        # 5. Decode Tilda's URL-encoded data
        # Tilda sends data as x-www-form-urlencoded, Frappe usually parses this correctly.
        # However, Tilda docs mention manual decoding might be needed for some chars (%40=@, %3a=:, etc.)
        # Let's assume frappe handles basic decoding, but apply unquote_plus for thoroughness.
        decoded_data = {}
        for key, value in request_data.items():
             # Tilda might send multiple values for a key (e.g., checkboxes) - Frappe puts them in a list.
             # We'll handle the simple case first: take the first value if it's a list.
             # More complex handling (e.g., joining list values) might be needed based on Tilda forms.
            processed_value = value
            if isinstance(value, list):
                 processed_value = value[0] # Take the first item for now

            if isinstance(processed_value, str):
                 decoded_data[unquote_plus(key)] = unquote_plus(processed_value)
            else:
                 decoded_data[unquote_plus(key)] = processed_value # Keep non-strings as is (numbers?)

        # 6. Optional: Create Initial Log Entry
        if config.enable_logging:
            try:
                log_doc = frappe.new_doc("Tilda Webhook Log")
                log_doc.webhook_configuration = config.name
                log_doc.received_at = received_at
                log_doc.status = "Processing"
                log_doc.payload = json.dumps(decoded_data, indent=2)
                log_doc.insert(ignore_permissions=True) # Requires System Manager/Admin role usually
                log_name = log_doc.name
                frappe.db.commit() # Commit log entry immediately
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
        frappe.log_error(message=traceback.format_exc(), title="Tilda Webhook Unhandled Error")
        frappe.local.response["http_status_code"] = 500
        # Optionally update log status to Error here if log_name exists
        if log_name:
            try:
                log_doc = frappe.get_doc("Tilda Webhook Log", log_name)
                log_doc.status = "Error"
                log_doc.message = f"Error during initial handling: {traceback.format_exc()}"
                log_doc.save(ignore_permissions=True)
                frappe.db.commit()
            except Exception as log_e:
                frappe.log_error(f"Failed to update log status after initial error for {log_name}: {log_e}", "Tilda Webhook Logging Error")

        return {"status": "error", "message": "Internal Server Error during webhook handling."}


# --- Background Job ---

def process_webhook_data(config_name: str, payload: dict, log_name: str = None):
    """
    Background job to process the received Tilda data and create a Frappe document.
    """
    start_time = time.time()
    log_doc = None
    if log_name:
        try:
            log_doc = frappe.get_doc("Tilda Webhook Log", log_name)
        except Exception as e:
            frappe.log_error(f"Failed to retrieve log doc {log_name} for processing: {e}", "Tilda Webhook Processing Error")
            log_name = None

    try:
        config = frappe.get_cached_doc("Tilda Webhook Configuration", config_name)
        target_doctype = config.target_doctype
        mappings = config.field_mappings
        default_values = config.default_values

        if not target_doctype:
            raise ValueError("Target Doctype is not configured.")
        if not mappings:
             raise ValueError("Field Mappings are not configured.")

        new_doc_data = {"doctype": target_doctype}
        target_meta = frappe.get_meta(target_doctype)

        # 1. Применяем маппинги из Tilda
        for mapping in mappings:
            tilda_field = mapping.tilda_field_name
            frappe_field = mapping.frappe_field_name
            if tilda_field in payload:
                value = payload[tilda_field]
                field_meta = target_meta.get_field(frappe_field)
                if field_meta:
                    try:
                        # --- Блок конвертации типов (без изменений) ---
                        converted_value = None
                        if field_meta.fieldtype in ["Int", "Check"]:
                            converted_value = cint(value)
                        elif field_meta.fieldtype in ["Float", "Currency", "Percent"]:
                             converted_value = flt(value)
                        elif field_meta.fieldtype in ["Date"]:
                            try: converted_value = get_datetime(value).date()
                            except Exception: converted_value = None
                        elif field_meta.fieldtype in ["Datetime"]:
                             try: converted_value = get_datetime(value)
                             except Exception: converted_value = None
                        else:
                             converted_value = value

                        if converted_value is not None: # Добавляем только если конвертация успешна
                             new_doc_data[frappe_field] = converted_value
                        elif value is None or value == '': # Allow setting null/empty if original was null/empty
                             new_doc_data[frappe_field] = None
                        # --- Конец блока конвертации ---
                    except Exception as e:
                         frappe.log_error(f"Error converting mapped value '{value}' for field '{frappe_field}' (Tilda: '{tilda_field}'): {e}", "Tilda Webhook Data Conversion Error")
                else:
                     frappe.log_error(f"Mapped Frappe field '{frappe_field}' not found in Doctype '{target_doctype}'.", "Tilda Webhook Configuration Error")

        # 2. Применяем значения по умолчанию
        if default_values:
            for default_entry in default_values:
                frappe_field = default_entry.frappe_field_name
                default_text_value = default_entry.default_value
                overwrite = default_entry.overwrite_if_exists

                # Применяем, если:
                # - поле еще не установлено ИЛИ
                # - разрешена перезапись
                if overwrite or frappe_field not in new_doc_data:
                    field_meta = target_meta.get_field(frappe_field)
                    if field_meta:
                        try:
                            # --- Блок конвертации типов для ЗНАЧЕНИЙ ПО УМОЛЧАНИЮ ---
                            converted_value = None
                            if default_text_value is None or default_text_value == '':
                                converted_value = None
                            elif field_meta.fieldtype in ["Int", "Check"]:
                                converted_value = cint(default_text_value)
                            elif field_meta.fieldtype in ["Float", "Currency", "Percent"]:
                                converted_value = flt(default_text_value)
                            elif field_meta.fieldtype in ["Date"]:
                                try: converted_value = get_datetime(default_text_value).date()
                                except Exception: converted_value = None
                            elif field_meta.fieldtype in ["Datetime"]:
                                try: converted_value = get_datetime(default_text_value)
                                except Exception: converted_value = None
                            # Add Link check example
                            # elif field_meta.fieldtype == "Link":
                            #    if frappe.db.exists(field_meta.options, default_text_value):
                            #         converted_value = default_text_value
                            #    else:
                            #         frappe.log_error(f"Default value Link '{default_text_value}' not found for field '{frappe_field}' (Target: {field_meta.options}).", "...")
                            #         converted_value = None
                            else:
                                converted_value = default_text_value
                            # --- Конец блока конвертации ---

                            if converted_value is not None or (default_text_value is None or default_text_value == ''):
                                 new_doc_data[frappe_field] = converted_value
                                 print(f"[Defaults] Set {frappe_field} = {converted_value}") # Отладка
                            elif default_text_value:
                                 frappe.log_error(f"Could not convert default value '{default_text_value}' for field '{frappe_field}' (type {field_meta.fieldtype}).", "Tilda Default Value Conversion Error")

                        except Exception as e:
                             frappe.log_error(f"Error converting default value '{default_text_value}' for field '{frappe_field}': {e}", "Tilda Default Value Error")
                    else:
                         frappe.log_error(f"Default value Frappe field '{frappe_field}' not found in Doctype '{target_doctype}'.", "Tilda Webhook Configuration Error")

        # 3. Создаем документ
        print("Final data for new doc:", new_doc_data)
        new_doc = frappe.new_doc(target_doctype)
        new_doc.update(new_doc_data)
        new_doc.insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.commit()

        if log_doc:
            log_doc.status = "Success"
            log_doc.message = f"Successfully created {target_doctype} {new_doc.name}"
            log_doc.set_created_document_link(target_doctype, new_doc.name)

    except Exception as e:
        frappe.db.rollback()
        error_message = traceback.format_exc()
        frappe.log_error(message=error_message, title=f"Tilda Webhook Processing Error ({config_name})")
        if log_doc:
            log_doc.status = "Error"
            log_doc.message = error_message
    finally:
        if log_doc:
            end_time = time.time()
            log_doc.processing_time_ms = int((end_time - start_time) * 1000)
            try:
                log_doc.save(ignore_permissions=True)
                frappe.db.commit()
            except Exception as log_e:
                 frappe.log_error(f"Failed to save final log status for {log_name}: {log_e}", "Tilda Webhook Logging Error") 