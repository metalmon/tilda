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

        # 3. Calculate URL using the NEW key
        site_url = frappe.utils.get_site_url(frappe.local.site)
        api_path = f"/api/method/tilda.frappe_tilda.webhook_handler.handle_webhook/{docname}?key={new_key}"
        full_url = urljoin(site_url, api_path)

        # 4. Atomically update webhook_url_html in DB
        print(f"Attempting atomic save for webhook_url_html in {docname}...")
        frappe.db.set_value("Tilda Webhook Configuration", docname, "webhook_url_html", full_url, update_modified=False)
        print(f"Atomic save for webhook_url_html completed for {docname}.")

        # 5. Return the calculated URL for JS update
        return {"webhook_url": full_url}

    except Exception as e:
        error_details = traceback.format_exc()
        frappe.log_error(error_details, f"utils.regenerate_tilda_key Error for {docname}")
        # Rollback might not be needed/possible depending on set_value behavior
        frappe.throw(f"Failed to regenerate key atomically for {docname}. Check error log. Error: {str(e)}")

# Важно: Метод _get_webhook_url_html остается в файле контроллера доктайпа,
# так как он привязан к логике самого документа.
# Если бы и его надо было перенести, потребовалось бы передавать больше параметров. 