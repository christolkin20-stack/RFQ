from django.urls import path
from . import views
from . import api_projects
from . import api_supplier
from . import api_quotes

urlpatterns = [
    path('', views.app, name='rfq_app'),
    path('bundle/<str:bundle_id>/', views.app, name='rfq_bundle_app'),
    path('mega-admin/', views.mega_admin_dashboard, name='rfq_mega_admin'),

    # API
    path('api/health', api_projects.health, name='api_health'),
    path('api/session/me', api_projects.session_me, name='api_session_me'),
    path('api/projects', api_projects.projects_collection, name='api_projects_collection'),
    path('api/projects/bulk', api_projects.projects_bulk, name='api_projects_bulk'),
    path('api/projects/reset', api_projects.projects_reset, name='api_projects_reset'),
    path('api/projects/<str:project_id>', api_projects.project_detail, name='api_project_detail'),
    path('api/projects/<str:project_id>/attachments', api_projects.project_attachments, name='api_project_attachments'),
    path('api/projects/<str:project_id>/access', api_projects.project_access, name='api_project_access'),
    path('api/projects/<str:project_id>/attachments/<str:attachment_id>', api_projects.project_attachment_detail, name='api_project_attachment_detail'),
    path('api/export', api_projects.export_data, name='api_export'),

    # Edit locks API
    path('api/locks/acquire', api_projects.locks_acquire, name='api_locks_acquire'),
    path('api/locks/heartbeat', api_projects.locks_heartbeat, name='api_locks_heartbeat'),
    path('api/locks/release', api_projects.locks_release, name='api_locks_release'),
    path('api/locks/status', api_projects.locks_status, name='api_locks_status'),
    path('api/locks/force_unlock', api_projects.locks_force_unlock, name='api_locks_force_unlock'),

    # Admin management API (in-app admin page backend)
    path('api/admin/users', api_projects.admin_users, name='api_admin_users'),
    path('api/admin/companies', api_projects.admin_companies, name='api_admin_companies'),
    path('api/admin/audit_logs', api_projects.admin_audit_logs, name='api_admin_audit_logs'),
    path('api/admin/locks', api_projects.admin_locks, name='api_admin_locks'),

    # Supplier Interaction API
    path('api/supplier_access/generate', api_supplier.supplier_access_generate, name='api_supplier_access_generate'),
    path('api/supplier_access/<str:token>/submit', api_supplier.supplier_portal_submit, name='api_supplier_portal_submit'),
    path('api/supplier_access/<str:token>/save_draft', api_supplier.supplier_portal_save_draft, name='api_supplier_portal_save_draft'),
    path('api/supplier_access/<str:token>/approve', api_supplier.supplier_access_approve, name='api_supplier_access_approve'),
    path('api/projects/<str:project_id>/supplier_access', api_supplier.project_supplier_access_list, name='api_project_supplier_access_list'),
    path('api/supplier_access/<str:token>/viewed', api_supplier.supplier_access_viewed, name='api_supplier_access_viewed'),
    path('api/supplier_access/<str:token>/reject', api_supplier.supplier_access_reject, name='api_supplier_access_reject'),
    path('api/supplier_access/<str:token>/request_reopen', api_supplier.supplier_access_request_reopen, name='api_supplier_access_request_reopen'),
    path('api/supplier_access/<str:token>/update_items', api_supplier.supplier_access_update_items, name='api_supplier_access_update_items'),
    path('api/supplier_access/<str:token>/cancel', api_supplier.supplier_access_cancel, name='api_supplier_access_cancel'),
    path('api/supplier_access/<str:token>/reopen', api_supplier.supplier_access_reopen_buyer, name='api_supplier_access_reopen_buyer'),
    path('api/supplier_access/bulk_generate', api_supplier.supplier_access_bulk_generate, name='api_supplier_access_bulk_generate'),
    path('api/supplier_interaction/file/<str:file_id>', api_supplier.supplier_interaction_file_download, name='api_supplier_interaction_file_download'),

    # Quotes API
    path('api/quotes/', api_quotes.quotes_list, name='api_quotes_list'),
    path('api/quotes/create/', api_quotes.quotes_create, name='api_quotes_create'),
    path('api/quotes/create_from_item/', api_quotes.quotes_create_from_item, name='api_quotes_create_from_item'),
    path('api/quotes/export_to_item/', api_quotes.quotes_export_to_item, name='api_quotes_export_to_item'),
    path('api/quotes/bulk_import/', api_quotes.quotes_bulk_import, name='api_quotes_bulk_import'),
    path('api/quotes/upsert_from_planner/', api_quotes.quotes_upsert_from_planner, name='api_quotes_upsert_from_planner'),
    # Detail patterns (wildcards) must come last
    path('api/quotes/<str:quote_id>/', api_quotes.quotes_detail, name='api_quotes_detail'),
    path('api/quotes/<str:quote_id>/update/', api_quotes.quotes_update, name='api_quotes_update'),
    path('api/quotes/<str:quote_id>/delete/', api_quotes.quotes_delete, name='api_quotes_delete'),

    # Portal View
    path('portal/<str:token>/', views.portal, name='rfq_portal'),
]
