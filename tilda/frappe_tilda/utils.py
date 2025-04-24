# Copyright (c) 2024, Bucher Industries AG and contributors
# For license information, please see license.txt

import frappe
import secrets
from urllib.parse import urlparse, urlunparse
import traceback # Import traceback

@frappe.whitelist()
def regenerate_tilda_key(docname: str):
    """
    Regenerates the secret key for a Tilda Webhook Configuration and returns the new URL.
    Ensures the generated key is unique across all webhook configurations.
    """
    try:
        # Generate a unique key
        max_attempts = 10  # Limit attempts to avoid infinite loop
        new_key = None
        
        for attempt in range(max_attempts):
            # Generate a new key
            new_key = secrets.token_urlsafe(32)
            
            # Check if this key is already used in another configuration
            duplicate_exists = frappe.db.exists(
                "Tilda Webhook Configuration", 
                {"secret_key": new_key, "name": ["!=", docname]}
            )
            
            # If key is unique, use it
            if not duplicate_exists:
                break
                
        if duplicate_exists and attempt == max_attempts - 1:
            frappe.throw("Could not generate a unique key after multiple attempts. Please try again.")

        # Update the key atomically WITHOUT updating modified timestamp
        frappe.db.set_value("Tilda Webhook Configuration", docname, "secret_key", new_key, update_modified=False)
        
        # Generate URL manually without loading the document
        # 1. Get site URL
        site_url = frappe.utils.get_site_url(frappe.local.site)
        
        # 2. Parse the URL and properly handle scheme and port
        parsed_url = urlparse(site_url)
        
        # Ensure HTTPS scheme
        if parsed_url.scheme == 'http':
            parsed_url = parsed_url._replace(scheme='https')
        
        # Fix the ":None" problem by rebuilding the netloc without the port if it's None
        if ":None" in parsed_url.netloc:
            clean_netloc = parsed_url.netloc.split(":")[0]  # Get just the hostname
            parsed_url = parsed_url._replace(netloc=clean_netloc)
        
        # Rebuild the clean URL
        clean_site_url = urlunparse(parsed_url)
        
        # Remove trailing slash if present
        if clean_site_url.endswith('/'):
            clean_site_url = clean_site_url[:-1]
            
        # 3. Create the API path with the new key
        api_path = f"/api/method/tilda.frappe_tilda.webhook_handler.handle_webhook?key={new_key}"
        
        # 4. Join site URL and API path
        webhook_url = clean_site_url + api_path
        
        # 5. Update the URL field in the document without triggering modified
        frappe.db.set_value("Tilda Webhook Configuration", docname, "webhook_url_html", webhook_url, update_modified=False)
        
        return {"webhook_url": webhook_url}

    except Exception as e:
        error_details = traceback.format_exc()
        frappe.log_error(error_details, f"utils.regenerate_tilda_key Error for {docname}")
        frappe.throw(f"Failed to regenerate key: {str(e)}")

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