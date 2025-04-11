# Copyright (c) 2025, Bucher Industries AG and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import csv
import io
import traceback

# Import the processing function from webhook_handler
from tilda.frappe_tilda.webhook_handler import process_webhook_data

class TildaCSVImporter(Document):
    def before_save(self):
        # Prevent saving if import is already in progress or completed?
        # For now, allow saving anytime.
        pass

    @frappe.whitelist()
    def start_import(self):
        """Method called by the 'Start Import' button."""
        if self.status not in ["Pending", "Failed"]:
            frappe.throw("Import is already processing or completed.")

        if not self.csv_file:
            frappe.throw("Please attach the Tilda Export CSV file first.")

        if not self.webhook_configuration:
            frappe.throw("Please select the Webhook Configuration to use for mapping.")

        # --- Correct way to clear child table and set status ---
        # Clear previous log by setting the list to empty and saving
        self.import_log = []
        self.status = "Processing"
        # Save the changes (status and empty log table) before enqueuing
        # Using ignore_permissions assuming the user clicking the button has write access
        try:
            self.save(ignore_permissions=True)
            # No need for extra commit here, save handles it.
        except Exception as e:
            frappe.log_error(traceback.format_exc(), "Tilda CSV Importer Save Error")
            frappe.throw(f"Failed to update importer status before starting job: {str(e)}")
        # ------------------------------------------------------

        # Enqueue the background job
        frappe.enqueue(
            "tilda.frappe_tilda.doctype.tilda_csv_importer.tilda_csv_importer.process_csv_import",
            queue="long", # Use long queue for potentially large files
            timeout=7200, # 2 hours timeout
            importer_name=self.name,
            # Pass necessary info to the background job
            webhook_config_name=self.webhook_configuration,
            csv_file_docname=self.csv_file
        )

        frappe.msgprint(f"Import started in the background for '{self.name}'. Check logs for progress.")

# --- Background Job Function --- (Defined outside the class)

def process_csv_import(importer_name: str, webhook_config_name: str, csv_file_docname: str):
    """Background job to read CSV and process each row using webhook logic."""
    importer_doc = frappe.get_doc("Tilda CSV Importer", importer_name)
    # Get the delimiter from the importer document
    delimiter = importer_doc.get("csv_delimiter", ";") # Default to semicolon if not set
    # Ensure delimiter is a single character, fallback if not
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        frappe.log_error(f"Invalid delimiter '{delimiter}' configured for importer {importer_name}. Falling back to ';'.", "Tilda CSV Import Config Error")
        delimiter = ";"

    processed_count = 0
    error_count = 0
    target_doc_type = None # To store the target doctype for log linking

    try:
        # Get CSV content
        # csv_file_docname now actually contains the file URL
        csv_file_url = csv_file_docname
        # Find the File document name based on the URL
        file_name = frappe.db.get_value("File", {"file_url": csv_file_url}, "name")

        if not file_name:
             raise ValueError(f"Could not find File document for URL: {csv_file_url}")

        file_doc = frappe.get_doc("File", file_name)
        csv_content = file_doc.get_content()

        # Decode content and read CSV
        # Use io.StringIO to treat the string content as a file
        # get_content() likely returns a string already, so no need to decode
        csv_data = io.StringIO(csv_content)
        # Use DictReader to access columns by header name
        # Specify the correct delimiter fetched from the document!
        reader = csv.DictReader(csv_data, delimiter=delimiter)

        # Get target doctype once from webhook config for logging
        try:
            webhook_config = frappe.get_doc("Tilda Webhook Configuration", webhook_config_name)
            target_doc_type = webhook_config.target_doctype
        except Exception:
            frappe.log_error(traceback.format_exc(), f"Tilda CSV Import {importer_name}: Failed to get webhook config")
            raise ValueError("Could not retrieve Webhook Configuration details.")

        # Process each row
        for i, row_dict in enumerate(reader):
            row_number = i + 2 # Account for header row (1-based index in files)
            payload = {} # This will be the payload for process_webhook_data
            log_entry = {
                "doctype": "Tilda Import Log Entry",
                "row_number": row_number,
                "status": "Error", # Default to Error
                "message": "",
                "target_document_type": target_doc_type
            }

            try:
                # --- ADD LOGGING HERE ---
                log_csv_row = f"[Tilda CSV Import {importer_name}] Processing Row {row_number}. Data from CSV: {row_dict}"
                frappe.logger().info(log_csv_row)
                print(log_csv_row) # Also print for visibility
                # ------------------------

                # Prepare payload (keys should match CSV headers)
                # We pass the whole row dict as the base payload
                payload = dict(row_dict)

                # Call the existing webhook processing function
                # It handles mapping, defaults, cookies, and document creation
                # It will raise an exception on failure
                # We don't need its return value here, rely on exceptions
                # We pass log_name=None as we are logging separately here
                frappe.call(
                    "tilda.frappe_tilda.webhook_handler.process_webhook_data",
                    config_name=webhook_config_name,
                    payload=payload,
                    log_name=None # We handle logging here
                )

                # If no exception, it was successful
                # Get the created doc name from the return value
                created_doc_name = frappe.local.response # frappe.call puts result in response

                log_entry["status"] = "Success"
                log_entry["message"] = f"Processed successfully."
                # Set the dynamic link field if name is returned
                if created_doc_name and isinstance(created_doc_name, str):
                    log_entry["created_document"] = created_doc_name
                processed_count += 1

            except Exception as e:
                error_msg = traceback.format_exc()
                frappe.log_error(error_msg, f"Tilda CSV Import {importer_name} Row {row_number} Error")
                log_entry["status"] = "Error"
                log_entry["message"] = f"Error processing row: {str(e)}"
                error_count += 1
            finally:
                # --- Correct way to add child table row --- 
                try:
                    # Append the dictionary directly to the parent's child table field
                    importer_doc.append("import_log", log_entry)
                    # Log success of appending (in memory)
                    print(f"--- [Tilda Process {webhook_config_name}] Row {row_number}: Log entry DICT appended: {log_entry.get('status')}, {log_entry.get('message', '')[:50]}... ---")
                except Exception as log_append_err:
                    # If appending the dict fails (less likely)
                    err_msg_log_append = f"Failed to append log entry dict for row {row_number}: {traceback.format_exc()}"
                    print(f"--- [Tilda Process {webhook_config_name}] Row {row_number}: ERROR appending log entry dict: {err_msg_log_append} ---")
                    frappe.log_error(err_msg_log_append, f"Tilda CSV Import {importer_name} Log Append Error")
                    # Optionally manually update status if append fails?
                    # importer_doc.status = "Failed"
                # --------------------------------------------

                # Update progress periodically? Save the parent doc which now includes the new log entry
                if (i + 1) % 50 == 0:
                    try:
                        importer_doc.save(ignore_permissions=True)
                        frappe.db.commit()
                    except Exception as periodic_save_err:
                         frappe.log_error(f"Error during periodic save for importer {importer_name} at row {row_number}: {traceback.format_exc()}", "Tilda CSV Import Periodic Save Error")

        # Final update after loop finishes - save the parent doc with all appended logs
        final_status = "Failed" if error_count > 0 else "Completed"
        importer_doc.status = final_status
        # No need to reassign importer_doc.import_log, append modified it in memory
        importer_doc.save(ignore_permissions=True)
        frappe.db.commit()

    except Exception as job_err:
        # Handle errors during file reading, setup, or final save
        error_msg = traceback.format_exc()
        frappe.log_error(error_msg, f"Tilda CSV Import {importer_name} Job Error")
        try:
            # Attempt to save the failure status and any logs collected so far
            importer_doc.status = "Failed"
            # Save the importer doc, which contains logs appended before the error
            importer_doc.save(ignore_permissions=True)
            frappe.db.commit()
        except Exception as final_save_err:
             frappe.log_error(f"Failed to save final error status for importer {importer_name}: {traceback.format_exc()}", "Tilda CSV Import Final Save Error") 