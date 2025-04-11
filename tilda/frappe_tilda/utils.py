# Copyright (c) 2024, Bucher Industries AG and contributors
# For license information, please see license.txt

import frappe
import secrets
from urllib.parse import urljoin
import traceback # Import traceback

print("--- Loading utils.py ---") # Отладочная печать

@frappe.whitelist()
def regenerate_tilda_key(docname: str):
    """
    Regenerates the secret key using frappe.db.set_value (atomic).
    """
    print(f"--- regenerate_tilda_key called from utils for doc: {docname} ---")
    try:
        # 1. Generate new key
        new_key = secrets.token_urlsafe(32)
        print(f"Generated new key for {docname}: {new_key[:5]}...")

        # 2. Atomically update secret_key in DB
        print(f"Attempting atomic save for secret_key in {docname}...")
        frappe.db.set_value("Tilda Webhook Configuration", docname, "secret_key", new_key, update_modified=False)
        print(f"Atomic save for secret_key completed for {docname}.")

        # 3. Get the document instance to call its _get_webhook_url method
        # This ensures we use the same logic (e.g., https, query params)
        try:
            doc = frappe.get_doc("Tilda Webhook Configuration", docname)
            # The get_doc call above will have the *old* key, but _get_webhook_url
            # uses get_password() which should read the *new* key just saved by set_value.
            # If get_password relies on the doc object's state, we might need to reload or pass the new key.
            # Let's assume get_password reads fresh from DB for now.
            correct_url = doc._get_webhook_url()
            if not correct_url or "Error" in correct_url:
                raise ValueError(f"_get_webhook_url returned an error or empty value: {correct_url}")
            print(f"Generated correct URL via doc._get_webhook_url for {docname}: {correct_url}")
        except Exception as url_exc:
             frappe.log_error(traceback.format_exc(), f"Error calling _get_webhook_url in regenerate_tilda_key for {docname}")
             frappe.throw(f"Failed to generate the new webhook URL for {docname}. Error: {str(url_exc)}")

        # 4. Atomically update webhook_url_html in DB with the CORRECT URL
        print(f"Attempting atomic save for webhook_url_html in {docname}...")
        frappe.db.set_value("Tilda Webhook Configuration", docname, "webhook_url_html", correct_url, update_modified=False)
        print(f"Atomic save for webhook_url_html completed for {docname}.")

        # 5. Return the calculated CORRECT URL for JS update
        return {"webhook_url": correct_url}

    except Exception as e:
        error_details = traceback.format_exc()
        frappe.log_error(error_details, f"utils.regenerate_tilda_key Error for {docname}")
        # Rollback might not be needed/possible depending on set_value behavior
        frappe.throw(f"Failed to regenerate key atomically for {docname}. Check error log. Error: {str(e)}")

# Важно: Метод _get_webhook_url_html остается в файле контроллера доктайпа,
# так как он привязан к логике самого документа.
# Если бы и его надо было перенести, потребовалось бы передавать больше параметров. 

def parse_tilda_cookies(cookies_string):
    """Parses the Tilda COOKIES string into a dictionary."""
    cookies_dict = {}
    if not cookies_string or not isinstance(cookies_string, str):
        return cookies_dict
    try:
        pairs = cookies_string.split(';')
        for pair in pairs:
            if '=' in pair:
                # Split only on the first equals sign
                key, value = pair.split('=', 1)
                # Strip whitespace from key and value
                key = key.strip()
                value = value.strip()
                if key: # Ensure key is not empty after stripping
                     cookies_dict[key] = value
    except Exception as e:
        # Log the error but return the dictionary parsed so far
        frappe.log_error(message=f"Error parsing Tilda cookies string: '{cookies_string}'. Error: {e}", title="Tilda Cookie Parsing Error")
    return cookies_dict

def apply_mappings_and_defaults(target_doctype, payload, cookies_string, field_mappings, default_values):
    """
    Applies field mappings and default values to create data for a new Frappe document.

    Args:
        target_doctype (str): The target Doctype name.
        payload (dict): The incoming data payload from Tilda (or CSV row).
        cookies_string (str): The raw COOKIES string (will be parsed internally).
        field_mappings (list): List of mapping definitions (child table dicts).
        default_values (list): List of default value definitions (child table dicts).

    Returns:
        dict: Data dictionary ready for frappe.get_doc().
    """
    new_doc_data = {"doctype": target_doctype}
    cookies_dict = parse_tilda_cookies(cookies_string)

    # 1. Apply Mappings
    if field_mappings:
        for mapping in field_mappings:
            frappe_field = mapping.get("frappe_field_name")
            tilda_fields_str = mapping.get("tilda_field_name", "")
            behavior = mapping.get("multi_field_behavior", "Overwrite")
            delimiter = mapping.get("concatenation_delimiter", " ")

            if not frappe_field:
                # frappe.logger().debug(f"[Tilda Mapping Util] Skipping mapping with no frappe_field_name: {mapping}")
                continue # Skip if Frappe field is not defined

            potential_tilda_fields = [f.strip() for f in tilda_fields_str.split(',') if f.strip()]
            if not potential_tilda_fields:
                 # frappe.logger().debug(f"[Tilda Mapping Util] Skipping mapping for '{frappe_field}' - no Tilda fields specified: {mapping}")
                 continue # Skip if no Tilda fields are listed

            found_values = []
            final_value = None

            # frappe.logger().debug(f"[Tilda Mapping Util] Processing mapping for '{frappe_field}'. Tilda fields: {potential_tilda_fields}, Behavior: {behavior}")

            # Iterate through potential Tilda field names for this mapping
            for tilda_field in potential_tilda_fields:
                current_value = None
                # Check cookies first
                if tilda_field in cookies_dict:
                    current_value = cookies_dict[tilda_field]
                    # frappe.logger().debug(f"[Tilda Mapping Util]   Found '{tilda_field}' in cookies: '{current_value}'")
                # Then check payload
                elif tilda_field in payload:
                    current_value = payload[tilda_field]
                    # frappe.logger().debug(f"[Tilda Mapping Util]   Found '{tilda_field}' in payload: '{current_value}'")
                # else:
                #     frappe.logger().debug(f"[Tilda Mapping Util]   '{tilda_field}' not found in cookies or payload.")


                # Process found value based on behavior
                # Ensure we only consider non-empty strings or non-None values
                if current_value is not None and current_value != '':
                    if behavior == "Overwrite":
                        final_value = current_value
                        # frappe.logger().debug(f"[Tilda Mapping Util]     Behavior=Overwrite. Setting final_value='{final_value}'. Breaking loop.")
                        break # Found first value, stop for Overwrite
                    elif behavior == "Concatenate":
                        found_values.append(str(current_value)) # Collect all values for Concatenate
                        # frappe.logger().debug(f"[Tilda Mapping Util]     Behavior=Concatenate. Appending '{current_value}'. Current found_values: {found_values}")

            # Consolidate concatenated values if applicable
            if behavior == "Concatenate" and found_values:
                 # Check if target field type supports concatenation (simple check for now)
                 is_text_like = True # Assume text-like unless known otherwise
                 try:
                     # Ensure target_doctype and frappe_field are valid before fetching meta
                     if target_doctype and frappe_field and frappe.db.exists("DocType", target_doctype):
                          meta = frappe.get_meta(target_doctype)
                          if meta:
                              field_meta = meta.get_field(frappe_field)
                              if field_meta and field_meta.fieldtype not in ["Data", "Small Text", "Text", "Long Text", "Text Editor", "Code", "HTML", "Read Only", "Select"]:
                                  is_text_like = False
                                  # frappe.logger().debug(f"[Tilda Mapping Util] Field '{frappe_field}' type '{field_meta.fieldtype}' is not text-like. Concatenation may not apply.")
                 except Exception as meta_ex:
                      frappe.log_error(f"Error checking field metadata for {target_doctype}.{frappe_field}: {meta_ex}", "Tilda Mapping Metadata Check")
                      # Proceed cautiously assuming text-like if metadata check fails

                 if is_text_like:
                      final_value = delimiter.join(found_values)
                      # frappe.logger().debug(f"[Tilda Mapping Util]   Concatenating values with delimiter '{delimiter}'. Result: '{final_value}'")
                 elif found_values: # Fallback to first value if not text-like
                      final_value = found_values[0]
                      # frappe.logger().debug(f"[Tilda Mapping Util]   Field not text-like. Falling back to first found value: '{final_value}'")

            # Assign the final determined value only if it's not None
            # Allows mapping to empty strings if the source value is an empty string
            if final_value is not None:
                 new_doc_data[frappe_field] = final_value
                 # frappe.logger().debug(f"[Tilda Mapping Util] Assigned '{frappe_field}' = '{final_value}'")
            # else:
            #     frappe.logger().debug(f"[Tilda Mapping Util] No value assigned for '{frappe_field}' after processing.")


    # 2. Apply Defaults (conditionally)
    if default_values:
        # frappe.logger().debug(f"[Tilda Mapping Util] Applying defaults: {default_values}")
        for default in default_values:
            frappe_field = default.get("frappe_field_name")
            default_value = default.get("default_value")
            overwrite = default.get("overwrite_mapped") # Get the value of the checkbox

            # Apply default if:
            # 1. Field is valid AND
            # 2. (Field was NOT mapped OR overwrite checkbox is checked)
            should_apply_default = False
            if frappe_field:
                if frappe_field not in new_doc_data:
                    should_apply_default = True # Apply if not mapped
                    # frappe.logger().debug(f"[Tilda Mapping Util] Condition met: Apply default for '{frappe_field}' (not mapped).")
                elif overwrite:
                    should_apply_default = True # Apply if overwrite is checked
                    # frappe.logger().debug(f"[Tilda Mapping Util] Condition met: Apply default for '{frappe_field}' (overwrite is True).")
                # else:
                     # frappe.logger().debug(f"[Tilda Mapping Util] Condition NOT met: Skipping default for '{frappe_field}' (already mapped and overwrite is False).")

            if should_apply_default:
                # Check if default_value is None, as empty string is a valid default
                if default_value is not None:
                    new_doc_data[frappe_field] = default_value
                    # frappe.logger().debug(f"[Tilda Mapping Util] Applied default '{frappe_field}' = '{default_value}'")
            # else:
            #      # Log why default was skipped if frappe_field was valid
            #      if frappe_field:
            #           if frappe_field in new_doc_data and not overwrite:
            #                frappe.logger().debug(f"[Tilda Mapping Util] Skipped default for '{frappe_field}' (already mapped, overwrite=False)")
            #           # else: frappe.logger().debug(f"[Tilda Mapping Util] Skipped default for '{frappe_field}' (reason unknown or already logged)")
            #      # else: frappe.logger().debug(f"[Tilda Mapping Util] Skipped default with missing frappe_field_name: {default}")


    # frappe.logger().debug(f"[Tilda Mapping Util] Final new_doc_data: {new_doc_data}")
    return new_doc_data 