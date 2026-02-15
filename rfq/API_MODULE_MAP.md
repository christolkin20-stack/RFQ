# RFQ API Module Map (refactor status)

## Active route modules
- `rfq/api_projects.py`
  - health
  - projects CRUD + bulk/reset
  - attachments
  - export (bridge)

- `rfq/api_supplier.py` *(bridge to `views_api` for now)*
  - supplier access generation
  - supplier portal submit/save draft
  - approve/reject/reopen/update
  - supplier interaction file download

- `rfq/api_quotes.py` *(bridge to `views_api` for now)*
  - quotes list/detail/create/update/delete
  - create from item / export to item / bulk import / planner upsert

## Shared helpers
- `rfq/api_common.py`
  - auth guard
  - same-origin guard
  - buyer username helper
  - JSON parser helper

## Legacy implementation
- `rfq/views_api.py`
  - still contains most business logic
  - being gradually reduced as code is moved to domain modules

## Next extraction targets
1. Move quote logic out of `views_api.py` into `api_quotes.py`
2. Move supplier logic out of `views_api.py` into `api_supplier.py`
3. Move export logic to dedicated `api_export.py`
