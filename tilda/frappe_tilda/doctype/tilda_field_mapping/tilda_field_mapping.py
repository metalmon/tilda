# Copyright (c) 2024, Bucher Industries AG and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import cint

class TildaFieldMapping(Document):
    pass

@frappe.whitelist()
def get_target_doctype_fields(doctype_name):
    """
    Returns a list of field data ({value, label, reqd}) for the given Doctype,
    excluding read-only, table, and certain standard/hidden fields.
    """
    if not doctype_name:
        return []

    if not frappe.db.exists("DocType", doctype_name):
        return [] # Or raise an error?

    meta = frappe.get_meta(doctype_name)
    fields = []
    ignore_fieldtypes = ["Read Only", "Table", "HTML", "Button", "Section Break", "Column Break", "Image"]
    ignore_fieldnames = [
        "naming_series", "amended_from", "parent", "parentfield", "parenttype",
        "creation", "modified", "modified_by", "owner", "_user_tags", "_comments",
        "_assign", "_liked_by", "docstatus"
    ]

    for df in meta.fields:
        if (
            df.fieldname not in ignore_fieldnames and
            df.fieldtype not in ignore_fieldtypes and
            not df.get("read_only") and # Use .get() for safety
            not df.get("hidden") and
            not df.get("write_only") # Добавим проверку write_only
        ):
            fields.append({
                "value": df.fieldname,
                "label": f"{df.label} ({df.fieldname})",
                "reqd": cint(df.reqd) # <<< Добавляем флаг обязательности
            })

    return sorted(fields, key=lambda x: x["label"]) 