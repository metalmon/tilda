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
        # Используем set_onload для передачи данных в UI без модификации self
        url_string = self._get_webhook_url()
        logs_html_string = self._get_logs_html()
        # Указываем имя поля из JSON ('webhook_url_html', т.к. мы откатили переименование)
        self.set_onload("webhook_url_html", url_string)
        self.set_onload("logs_html", logs_html_string)

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

    def _get_logs_html(self):
        """Computes and returns the HTML for the recent logs view."""
        if not getattr(self, 'name', None) or not self.enable_logging:
            return "<div>Logging is disabled or document not saved.</div>"

        try:
            doc_name = self.name
            logs = frappe.get_list(
                "Tilda Webhook Log",
                filters={"webhook_configuration": doc_name},
                fields=["name", "creation", "status", "message"],
                order_by="creation desc",
                limit=5
            )

            if not logs:
                html = "<div>No logs found for this configuration yet.</div>"
            else:
                html = """
                <table class="table table-bordered table-condensed" style="font-size: 12px;">
                    <thead>
                        <tr>
                            <th style="width: 15%;">Log ID</th>
                            <th style="width: 20%;">Timestamp</th>
                            <th style="width: 10%;">Status</th>
                            <th>Message</th>
                        </tr>
                    </thead>
                    <tbody>
                """
                for log in logs:
                    status_color = {
                        "Success": "text-success",
                        "Error": "text-danger",
                        "Processing": "text-warning"
                    }.get(log.status, "")
                    log_link = frappe.utils.get_link_to_form("Tilda Webhook Log", log.name)
                    message_html = frappe.utils.escape_html(log.message or "")
                    # Truncate long messages for display in table
                    if len(message_html) > 150:
                        message_html = message_html[:150] + "..."

                    html += f"""
                        <tr>
                            <td><a href="{log_link}">{log.name}</a></td>
                            <td>{frappe.utils.format_datetime(log.creation, "dd-MM-yyyy HH:mm:ss")}</td>
                            <td class="{status_color}">{log.status}</td>
                            <td><div style="word-wrap: break-word;">{message_html}</div></td>
                        </tr>
                    """
                html += """
                    </tbody>
                </table>
                """
                list_view_link = frappe.utils.get_link_to_list("Tilda Webhook Log")
                escaped_name = frappe.utils.escape_html(doc_name)
                html += f'<p><a href="{list_view_link}?webhook_configuration={escaped_name}">View All Logs for this Configuration</a></p>'

            return html
        except Exception as e:
            frappe.log_error(f"Error generating logs HTML for {self.name}: {e}", "Webhook Log HTML Generation Error")
            return "<div>Error loading logs. Check system logs.</div>"

# You will need to create the Tilda Webhook Log Doctype later.
# You will also need to create the API endpoint handle_webhook later. 