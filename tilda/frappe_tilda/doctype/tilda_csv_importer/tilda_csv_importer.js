// Copyright (c) 2025, Metalmon and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Tilda CSV Importer", {
// 	refresh(frm) {

// 	},
// });

frappe.ui.form.on('Tilda CSV Importer', {
	refresh: function(frm) {
		// Always clear the primary action first on refresh
		frm.page.clear_primary_action();

		// Set the primary action button if the document is saved
		// and status is Pending or Failed
		if (!frm.is_new() && (frm.doc.status === 'Pending' || frm.doc.status === 'Failed')) {
			frm.page.set_primary_action(__('Start Import'), () => {
				// Show confirmation or progress indicator
				frappe.show_alert({
					message: __('Starting import process...'),
					indicator: 'blue'
				}, 5);

				// Call the Python method via frappe.call
				// Note: We are calling the method directly on the document instance
				// by passing `doc: frm.doc` and the method name.
				frappe.call({
					doc: frm.doc, // Pass the document instance
					method: 'start_import', // Method name in the Python class
					callback: function(r) {
						// Backend method start_import handles msgprint and errors
						// We just need to reload the doc to reflect status changes
						if (!r.exc) {
							 frm.reload_doc();
						} else {
							// Error already shown by frappe
						}
					},
					error: function(r) {
						 // Handle framework-level errors (e.g., network issues)
						 frappe.show_alert({
							  message: __('An error occurred while trying to start the import.'),
							  indicator: 'red'
						 }, 5);
					}
				});
			});
		}
	}
});
 