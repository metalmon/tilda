# Copyright (c) 2024, Bucher Industries AG and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import get_link_to_form

class TildaWebhookLog(Document):
    def set_created_document_link(self, doctype, docname):
        """Sets the HTML link for the successfully created document."""
        if doctype and docname:
            link = get_link_to_form(doctype, docname)
            self.created_document_link = f'<a href="{link}">View Created {doctype}</a>'
        else:
            self.created_document_link = ""

# Example usage within the background job after successful creation:
# log_doc = frappe.get_doc("Tilda Webhook Log", log_name)
# log_doc.set_created_document_link(target_doctype, new_doc.name)
# log_doc.save(ignore_permissions=True) # Run as admin/background user 