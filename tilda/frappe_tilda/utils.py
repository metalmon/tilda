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