// Copyright (c) 2024, Bucher Industries AG and contributors
// For license information, please see license.txt

frappe.ui.form.on('Tilda Webhook Configuration', {
    refresh: function(frm) {
        frm.set_df_property('ID', 'visibility', 'hidden');
        // Обновляем опции для дочерних таблиц при загрузке
        frm.trigger('set_field_mapping_query');
        frm.trigger('set_default_values_query');

        // Инициализируем переменную для отслеживания двойного клика
        frm._last_processed_target_doctype = frm.doc.target_doctype;

        let docinfo = frm.get_docinfo();
        console.log("Tilda Config Refresh: docinfo =", docinfo); // Log the entire docinfo object
        if (docinfo) {
            // Обновляем URL (Small Text)
            if (docinfo.webhook_url_html) {
                 frappe.model.set_value(frm.doctype, frm.docname, 'webhook_url_html', docinfo.webhook_url_html);
                 frm.refresh_field('webhook_url_html');
            }
        } else {
            console.log("Tilda Config Refresh: docinfo is null or undefined."); // Log if docinfo itself is missing
        }

        // Убеждаемся, что обработчик кнопки навешан (без лишнего кода в refresh)
        if (!frm.custom_regenerate_listener_attached) {
            frm.fields_dict['regenerate_key_button'].$input.off('click').on('click', function() {
                if (frm.is_new()) {
                    frappe.throw(__("Please save the document before regenerating the key."));
                    return;
                }
                frappe.confirm(
                    __('Regenerating the key will require updating the Webhook URL in Tilda. Do you want to proceed?'),
                    () => {
                        frm.call({
                            method: 'tilda.frappe_tilda.utils.regenerate_tilda_key',
                            args: { docname: frm.doc.name },
                            callback: function(r) {
                                console.log("Response from regenerate_tilda_key:", r);
                                if (!r.exc && r.message && r.message.webhook_url) {
                                    console.log("Updating webhook_url_html (as text) with:", r.message.webhook_url);
                                    frappe.model.set_value(frm.doctype, frm.docname, 'webhook_url_html', r.message.webhook_url);
                                    frm.refresh_field('webhook_url_html');
                                } else {
                                     console.error("Failed to get webhook_url from server response:", r);
                                }
                            }
                        });
                    },
                    () => {
                        console.log("Key regeneration cancelled.");
                    }
                );
            });
            frm.custom_regenerate_listener_attached = true;
        }
    },

    target_doctype: function(frm) {
        let current_doctype = frm.doc.target_doctype;

        // Check if this value is the same as the one we last processed
        if (current_doctype === frm._last_processed_target_doctype) {
            console.log("target_doctype event ignored, value already processed:", current_doctype);
            return;
        }

        // If it's a new value, update the tracking variable immediately
        frm._last_processed_target_doctype = current_doctype;
        console.log("target_doctype event processing new value:", current_doctype);

        // Если Doctype выбран
        if (current_doctype) {
            // Спрашиваем пользователя, если таблица уже заполнена
            if (frm.doc.field_mappings && frm.doc.field_mappings.length > 0) {
                frappe.confirm(
                    __('Clear existing mappings/defaults and auto-add required fields?'),
                    () => { // Если пользователь согласился
                        frm.trigger('update_field_mappings');
                    },
                    () => { // Если пользователь отказался, просто обновим опции
                        frm.trigger('set_field_mapping_query');
                        frm.trigger('set_default_values_query');
                    }
                );
            } else { // Если таблица пуста, просто обновляем
                frm.trigger('update_field_mappings');
            }
        } else { // Если Doctype очищен
            frm.set_value('field_mappings', []);
            frm.set_value('default_values', []);
            frm.trigger('set_field_mapping_query');
            frm.trigger('set_default_values_query');
        }
    },

    // Новая функция для обновления таблицы и опций
    update_field_mappings: function(frm) {
        frappe.call({
            method: 'tilda.frappe_tilda.doctype.tilda_field_mapping.tilda_field_mapping.get_target_doctype_fields',
            args: {
                doctype_name: frm.doc.target_doctype
            },
            callback: function(r) {
                if (r.message) {
                    let all_fields = r.message;
                    let required_fields = all_fields.filter(df => df.reqd === 1);

                    // Очищаем текущие строки
                    frm.set_value('field_mappings', []);
                    frm.set_value('default_values', []);

                    // Добавляем строки для обязательных полей
                    required_fields.forEach(df => {
                        let new_row = frm.add_child('field_mappings');
                        new_row.frappe_field_name = df.value;
                        // new_row.tilda_field_name = ''; // Оставляем пустым
                    });

                    // Обновляем опции для выпадающего списка ВСЕМИ полями
                    let field_options = all_fields.map(f => f.value);
                    frm.fields_dict['field_mappings'].grid.update_docfield_property(
                        'frappe_field_name', 'options', field_options
                    );
                    frm.fields_dict['default_values'].grid.update_docfield_property(
                        'frappe_field_name', 'options', field_options
                    );

                    // Обновляем грид
                    frm.refresh_field('field_mappings');
                    frm.refresh_field('default_values');
                }
            }
        });
    },

    // Старая функция для обновления только опций (используется, если пользователь отказался очищать)
    set_field_mapping_query: function(frm) {
        let target_doctype = frm.doc.target_doctype;
        let grid = frm.fields_dict['field_mappings'].grid;

        if (target_doctype && grid) {
            frappe.call({
                method: 'tilda.frappe_tilda.doctype.tilda_field_mapping.tilda_field_mapping.get_target_doctype_fields',
                args: {
                    doctype_name: target_doctype
                },
                callback: function(r) {
                    if (r.message) {
                        let field_options = r.message.map(f => f.value);
                        grid.update_docfield_property(
                            'frappe_field_name', 'options', field_options
                        );
                        grid.refresh();
                    }
                }
            });
        } else if (grid) {
            grid.update_docfield_property('frappe_field_name', 'options', []);
            grid.refresh();
        }
    },

    // Новая функция для обновления опций полей значений по умолчанию
    set_default_values_query: function(frm) {
        let target_doctype = frm.doc.target_doctype;
        let grid = frm.fields_dict['default_values'] ? frm.fields_dict['default_values'].grid : null; // Check if grid exists

        if (target_doctype && grid) {
            frappe.call({
                method: 'tilda.frappe_tilda.doctype.tilda_field_mapping.tilda_field_mapping.get_target_doctype_fields', // Reuse the same method
                args: {
                    doctype_name: target_doctype
                },
                callback: function(r) {
                    if (r.message) {
                        let field_options = r.message.map(f => f.value);
                        grid.update_docfield_property(
                            'frappe_field_name', 'options', field_options
                        );
                        grid.refresh();
                    }
                }
            });
        } else if (grid) {
            grid.update_docfield_property('frappe_field_name', 'options', []);
            grid.refresh();
        }
    }

    // field_mappings_on_load is often unnecessary if refresh and target_doctype triggers handle it.
    // field_mappings_on_load: function(frm) {
    //      frm.trigger('set_field_mapping_query');
    // }
});

// Also handle the field mapping on row add/remove if needed
frappe.ui.form.on('Tilda Field Mapping', {
    // form_render: function(frm, cdt, cdn) {
    // },
    // before_field_mappings_remove: function(frm, cdt, cdn) {
    // }
});