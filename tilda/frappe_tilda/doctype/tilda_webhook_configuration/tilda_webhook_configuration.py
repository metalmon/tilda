# Copyright (c) 2024, Bucher Industries AG and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import secrets
from urllib.parse import urljoin

class TildaWebhookConfiguration(Document):

    def before_save(self):
        if not self.secret_key:
            # Ensure key generation happens if empty
            self.secret_key = secrets.token_urlsafe(32)
        # Ensure URL field is updated before save if key exists
        if self.secret_key:
             self.webhook_url_html = self._get_webhook_url()

    def onload(self):
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

        # Set values needed by the client
        url_string = self._get_webhook_url()
        # logs_html_string = self._get_logs_html() # Removed

        self.set_onload("webhook_url_html", url_string)
        # self.set_onload("logs_html", logs_html_string) # Removed

        frappe.logger().info(f"[Tilda Onload] Processing onload for {self.name}")

    def validate(self):
        if self.target_doctype:
            if not frappe.db.exists("DocType", self.target_doctype):
                frappe.throw(f"Target Doctype '{self.target_doctype}' does not exist.")

    def _get_webhook_url(self):
        """Computes and returns the webhook URL string."""
        if getattr(self, 'name', None) and self.secret_key:
            try:
                doc_name = self.name
                site_url = frappe.utils.get_site_url(frappe.local.site)
                # Используем get_password для получения реального ключа
                real_secret_key = self.get_password('secret_key')
                if not real_secret_key:
                     return "Error: Secret key not found or inaccessible."

                api_path = f"/api/method/tilda.frappe_tilda.webhook_handler.handle_webhook/{doc_name}?key={real_secret_key}"
                full_url = urljoin(site_url, api_path)
                return full_url
            except Exception as e:
                frappe.log_error(f"Error generating webhook URL string for {self.name}: {e}", "Webhook URL Generation Error")
                return "Error generating URL. Check logs."
        else:
             return "Save the document to generate the Webhook URL."

# You will need to create the Tilda Webhook Log Doctype later.
# You will also need to create the API endpoint handle_webhook later. 