# Community content API

The community_items table stores three public feeds: events, projects, and gallery. The API is mounted below /api/v1/community.

## Public feeds

- GET /community/events
- GET /community/projects
- GET /community/gallery

Only published and non-archived entries are returned. Admins can pass ?include_unpublished=true to preview drafts.

## Admin operations

- POST /community/ creates an unpublished item.
- PATCH /community/{item_id} updates metadata or publishes/unpublishes an item.
- DELETE /community/{item_id} archives an item.

Create payload fields: kind, title, slug, optional description, image_url, external_url, location, starts_at, metadata, and order_index. Slugs must be lowercase URL-safe strings and unique. Only tech_admin and content_admin roles can manage entries. All operations are audit logged.

## Migration

The schema is introduced by Alembic revision 004_community_content, after 003_clerk_auth_policies. Apply it with uv run alembic upgrade head before deploying the updated Worker.
