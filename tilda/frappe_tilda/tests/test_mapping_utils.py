# tilda/frappe_tilda/tests/test_mapping_utils.py

import unittest
import frappe # Import frappe if needed for types or context, often mocked
# Import the function to test
from tilda.frappe_tilda.utils import apply_mappings_and_defaults

# Mock frappe.get_meta if necessary, otherwise assume basic field types
# from unittest.mock import patch, MagicMock

# --- Mocking Frappe environment for testing outside a live instance ---
# This is a simplified mock. For complex scenarios, you might need more elaborate mocking.
class MockField:
    def __init__(self, fieldname, fieldtype="Data"):
        self.fieldname = fieldname
        self.fieldtype = fieldtype

class MockMeta:
    def __init__(self, doctype_name, fields):
        self.name = doctype_name
        self._fields = {f.fieldname: f for f in fields}

    def get_field(self, fieldname):
        return self._fields.get(fieldname)

# Patch frappe functions if needed (example)
# @patch('frappe.get_meta')
# @patch('frappe.db.exists')
class TestTildaMappingUtils(unittest.TestCase):

    def setUp(self):
        self.target_doctype = "Test DocType"
        # Basic mock meta setup - adjust field types as needed for tests
        self.mock_meta = MockMeta(self.target_doctype, [
            MockField("full_name", "Data"),
            MockField("email_id", "Data"),
            MockField("campaign_source", "Data"),
            MockField("campaign_medium", "Data"),
            MockField("lead_name", "Data"),
            MockField("lead_source", "Data"),
            MockField("customer_name", "Data"),
            MockField("tags", "Small Text"), # Small Text allows concatenation
            MockField("status", "Data"),
            MockField("title", "Data"),
            MockField("details", "Text"),
            MockField("non_text_field", "Int"), # Example non-text field
            MockField("first_name", "Data"),
            MockField("age", "Int")
        ])
        # --- If using unittest.mock patching ---
        # self.patcher_exists = patch('frappe.db.exists', return_value=True)
        # self.mock_db_exists = self.patcher_exists.start()
        # self.patcher_get_meta = patch('frappe.get_meta', return_value=self.mock_meta)
        # self.mock_frappe_get_meta = self.patcher_get_meta.start()

        # --- Manual "patching" for simplicity if mocks aren't complex ---
        self._original_frappe_get_meta = getattr(frappe, 'get_meta', None)
        self._original_frappe_db_exists = getattr(frappe, 'db', None) and getattr(frappe.db, 'exists', None)
        frappe.get_meta = lambda dt: self.mock_meta if dt == self.target_doctype or dt == "CRM Lead" else (
            self._original_frappe_get_meta(dt) if self._original_frappe_get_meta else None
        )
        # Mock frappe.db and its exists method carefully
        if not getattr(frappe, 'db', None):
            frappe.db = lambda: None # Create a dummy db object if it doesn't exist
        frappe.db.exists = lambda *args, **kwargs: True # Assume doctype exists

    def tearDown(self):
        # Restore original functions only if they were stored
        if self._original_frappe_get_meta:
            frappe.get_meta = self._original_frappe_get_meta
        if self._original_frappe_db_exists:
            # This assumes frappe.db existed originally
            # If frappe.db was created in setUp, this might be tricky. Let's assume it existed.
            try:
                 frappe.db.exists = self._original_frappe_db_exists
            except AttributeError: # Handle case where frappe.db might have been removed or changed
                 pass
        # --- If using unittest.mock patching ---
        # self.patcher_exists.stop()
        # self.patcher_get_meta.stop()

    def test_basic_payload_mapping(self):
        payload = {"name": "John Doe", "email": "john@example.com"}
        cookies = ""
        mappings = [
            {"frappe_field_name": "full_name", "tilda_field_name": "name"},
            {"frappe_field_name": "email_id", "tilda_field_name": "email"}
        ]
        defaults = []
        expected = {"doctype": self.target_doctype, "full_name": "John Doe", "email_id": "john@example.com"}
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)

    def test_cookie_mapping(self):
        payload = {"some_data": "abc"}
        cookies = "utm_source=google;utm_medium=cpc"
        mappings = [
            {"frappe_field_name": "campaign_source", "tilda_field_name": "utm_source"},
            {"frappe_field_name": "campaign_medium", "tilda_field_name": "utm_medium"}
        ]
        defaults = []
        expected = {"doctype": self.target_doctype, "campaign_source": "google", "campaign_medium": "cpc"}
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)

    def test_payload_over_cookie_priority(self):
        # This tests if the *order of check* (cookies first) works correctly for different target fields
        payload = {"name": "John Payload"}
        cookies = "name=Jane Cookie; source=cookie_source" # Both 'name' and 'source' exist
        mappings = [
            # Rule 1: Map 'name' from Tilda (payload or cookie) to 'lead_name'
            {"frappe_field_name": "lead_name", "tilda_field_name": "name"},
            # Rule 2: Map 'source' from Tilda (payload or cookie) to 'lead_source'
            {"frappe_field_name": "lead_source", "tilda_field_name": "source"}
        ]
        defaults = []
        # For lead_name: 'name' is checked in cookies ('Jane Cookie'), then payload ('John Payload'). Overwrite takes the first found (cookie).
        # For lead_source: 'source' is checked in cookies ('cookie_source'), then payload (not found). Overwrite takes the cookie value.
        # Correction: The logic checks cookie *then* payload within the *same* mapping rule if the field name is the same.
        # If separate rules target the *same* Tilda field, the *last* rule would win.
        # Let's refine the test: Check cookie preference for the *same* Tilda field.
        payload = {"name": "John Payload"}
        cookies = "name=Jane Cookie"
        mappings = [
            {"frappe_field_name": "lead_name", "tilda_field_name": "name"}
        ]
        # Expected: Cookie value "Jane Cookie" should be taken first for "name"
        expected = {"doctype": self.target_doctype, "lead_name": "Jane Cookie"}
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)


    def test_multi_field_overwrite(self):
        payload = {"first_name": "Peter", "user_name": "pete", "login": "peter_login"}
        cookies = "user_name=pete_cookie" # Cookie value exists for user_name
        mappings = [
            # Checks cookie(first_name), payload(first_name), cookie(user_name), payload(user_name), cookie(login), payload(login)
            {"frappe_field_name": "customer_name", "tilda_field_name": "first_name, user_name, login", "multi_field_behavior": "Overwrite"}
        ]
        defaults = []
        # Should find "Peter" in payload(first_name) first and stop.
        expected = {"doctype": self.target_doctype, "customer_name": "Peter"}
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)

    def test_multi_field_overwrite_cookie_priority_within_rule(self):
        payload = {"login": "peter_login"} # No first_name or user_name in payload
        cookies = "user_name=pete_cookie" # user_name exists only in cookie
        mappings = [
            # Check order: cookie(first_name)[no], payload(first_name)[no], cookie(user_name)[yes!], ... stop.
            {"frappe_field_name": "customer_name", "tilda_field_name": "first_name, user_name, login", "multi_field_behavior": "Overwrite"}
        ]
        defaults = []
        # Should take user_name from cookie as it's found first in the specified order.
        expected = {"doctype": self.target_doctype, "customer_name": "pete_cookie"}
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)


    def test_multi_field_concatenate_default_delimiter(self):
        payload = {"fname": "Alice", "lname": "Smith"}
        cookies = "mname=J" # Middle initial from cookie
        mappings = [
            # Checks: cookie(fname)[no], payload(fname)[yes], cookie(mname)[yes], payload(mname)[no], cookie(lname)[no], payload(lname)[yes]
            # Values found: "Alice", "J", "Smith"
            {"frappe_field_name": "full_name", "tilda_field_name": "fname, mname, lname", "multi_field_behavior": "Concatenate"}
        ]
        defaults = []
        # Default delimiter is space " "
        expected = {"doctype": self.target_doctype, "full_name": "Alice J Smith"}
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)

    def test_multi_field_concatenate_custom_delimiter(self):
        payload = {"tag1": "urgent", "tag3": "review"}
        cookies = "tag2=internal"
        mappings = [
            # Checks: c(tag1)[no], p(tag1)[yes], c(tag2)[yes], p(tag2)[no], c(tag3)[no], p(tag3)[yes]
            # Values: "urgent", "internal", "review"
            {"frappe_field_name": "tags", "tilda_field_name": "tag1,tag2,tag3", "multi_field_behavior": "Concatenate", "concatenation_delimiter": ", "}
        ]
        defaults = []
        expected = {"doctype": self.target_doctype, "tags": "urgent, internal, review"}
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)

    def test_missing_tilda_field(self):
        payload = {"email": "test@test.com"}
        cookies = ""
        mappings = [
            {"frappe_field_name": "full_name", "tilda_field_name": "name"} # 'name' is missing
        ]
        defaults = []
        expected = {"doctype": self.target_doctype} # Should map nothing for full_name
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)

    def test_apply_defaults(self):
        payload = {"name": "Test"}
        cookies = ""
        mappings = [{"frappe_field_name": "title", "tilda_field_name": "name"}]
        defaults = [
            {"frappe_field_name": "status", "default_value": "New"},
            {"frappe_field_name": "lead_source", "default_value": "Website"}
        ]
        expected = {"doctype": self.target_doctype, "title": "Test", "status": "New", "lead_source": "Website"}
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)

    def test_defaults_do_not_overwrite_mapping_by_default(self):
        # Test that defaults DO NOT overwrite mappings if overwrite_mapped is False/0/None
        payload = {"status": "From Payload"}
        cookies = ""
        mappings = [{"frappe_field_name": "status", "tilda_field_name": "status"}]
        defaults = [
            {"frappe_field_name": "status", "default_value": "Default Value", "overwrite_mapped": 0}
        ]
        # Expected: value from payload should be kept
        expected = {"doctype": self.target_doctype, "status": "From Payload"}
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)

    def test_defaults_do_overwrite_mapping_when_checked(self):
        # Test that defaults DO overwrite mappings if overwrite_mapped is True/1
        payload = {"status": "From Payload"}
        cookies = ""
        mappings = [{"frappe_field_name": "status", "tilda_field_name": "status"}]
        defaults = [
            {"frappe_field_name": "status", "default_value": "Default Value", "overwrite_mapped": 1}
        ]
        # Expected: default value should overwrite the payload value
        expected = {"doctype": self.target_doctype, "status": "Default Value"}
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)

    def test_empty_inputs(self):
        payload = {}
        cookies = ""
        mappings = []
        defaults = []
        expected = {"doctype": self.target_doctype}
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)

    def test_mapping_to_empty_string(self):
        payload = {"description": ""} # Explicitly empty
        cookies = ""
        mappings = [{"frappe_field_name": "details", "tilda_field_name": "description"}]
        defaults = []
        # The logic currently skips empty strings (''), let's verify this
        # expected = {"doctype": self.target_doctype, "details": ""} # Ideal: map the empty string
        expected = {"doctype": self.target_doctype} # Current behavior: skips empty string
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)
        # If we want to map empty strings, the condition `if current_value is not None and current_value != '':`
        # in utils.py needs to be changed to `if current_value is not None:`

    def test_concatenate_non_string_fallback(self):
        # Test concatenation fallback for non-text target field ('age' is Int)
        payload = {"part1": "30", "part2": "5"} # Simulate age parts
        cookies = ""
        mappings = [
            {"frappe_field_name": "age", "tilda_field_name": "part1, part2", "multi_field_behavior": "Concatenate"}
        ]
        defaults = []
        # Since 'age' is Int, concatenation shouldn't happen. Fallback takes the *first* value found ('30').
        expected = {"doctype": self.target_doctype, "age": "30"} # Fallback uses first value as string
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)
        # Note: The value is still a string here. Actual type conversion happens later in Frappe's ORM.

    def test_your_specific_case_overwrite(self):
        # Your case: Name, Name2 -> first_name (Overwrite), default status
        payload = {"Name": "Иван", "Name2": "Петров", "Email": "ivan@example.com"}
        cookies = ""
        mappings = [
             # Checks: c(Name)[no], p(Name)[yes], ... stop
            {"frappe_field_name": "first_name", "tilda_field_name": "Name, Name2", "multi_field_behavior": "Overwrite"}
        ]
        defaults = [
            {"frappe_field_name": "status", "default_value": "New"}
        ]
        target_doctype = "CRM Lead" # Use specific doctype for this test
        # Mock 'CRM Lead' meta if needed, assuming 'first_name' and 'status' exist
        self.mock_meta = MockMeta(target_doctype, [MockField("first_name"), MockField("status")])
        expected = {"doctype": target_doctype, "first_name": "Иван", "status": "New"}
        result = apply_mappings_and_defaults(target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)

    def test_your_specific_case_concatenate(self):
        # Your case: Name, Name2 -> first_name (Concatenate), default status
        payload = {"Name": "Иван", "Name2": "Петров", "Email": "ivan@example.com"}
        cookies = ""
        mappings = [
             # Checks: c(Name)[no], p(Name)[yes], c(Name2)[no], p(Name2)[yes]
             # Values: "Иван", "Петров"
            {"frappe_field_name": "first_name", "tilda_field_name": "Name, Name2", "multi_field_behavior": "Concatenate", "concatenation_delimiter": " "}
        ]
        defaults = [
            {"frappe_field_name": "status", "default_value": "New"}
        ]
        target_doctype = "CRM Lead" # Use specific doctype
        self.mock_meta = MockMeta(target_doctype, [MockField("first_name", "Data"), MockField("status", "Data")]) # Ensure first_name is text-like
        expected = {"doctype": target_doctype, "first_name": "Иван Петров", "status": "New"}
        result = apply_mappings_and_defaults(target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)

    def test_default_value_is_empty_string(self):
        payload = {}
        cookies = ""
        mappings = []
        defaults = [{"frappe_field_name": "details", "default_value": ""}] # Default is empty string
        expected = {"doctype": self.target_doctype, "details": ""} # Default empty string should be applied
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)

    def test_mapping_empty_tilda_field_list(self):
        payload = {"data": "abc"}
        cookies = ""
        mappings = [
            {"frappe_field_name": "field1", "tilda_field_name": "   "}, # Empty after strip
            {"frappe_field_name": "field2", "tilda_field_name": ","} # Empty after split and strip
        ]
        defaults = []
        expected = {"doctype": self.target_doctype} # No fields should be mapped
        result = apply_mappings_and_defaults(self.target_doctype, payload, cookies, mappings, defaults)
        self.assertDictEqual(result, expected)


# This allows running the tests from the command line
# Example: python -m unittest tilda.frappe_tilda.tests.test_mapping_utils
if __name__ == '__main__':
    # You might need to initialize Frappe context partially for logging etc.
    # try:
    #     frappe.init(site='your_test_site') # Replace with your site if needed
    #     frappe.connect()
    # except Exception:
    #     print("Could not initialize frappe context, tests might fail if they depend on DB or full context.")
    unittest.main() 