from fastapi import APIRouter

from app.api.v1 import (
    admin_data,
    admin_jobs,
    admin_users,
    analytics,
    auth,
    bookmarks,
    community,
    dashboard,
    drafts,
    health,
    profile,
    progress,
    resources,
    roadmap,
    webhooks,
)

api_router = APIRouter()

# Core & Health
api_router.include_router(health.router)

# 1. Student Authentication & Clerk Webhooks
api_router.include_router(auth.router)
api_router.include_router(webhooks.router)
api_router.include_router(community.router)

# 2. Admin User Management & Permissions
api_router.include_router(admin_users.router)

# 3. Student Profiles
api_router.include_router(profile.router)

# 4. Roadmap, Topics, Subtopics & Ordering
api_router.include_router(roadmap.router)

# 5. Resources & Availability Probing
api_router.include_router(resources.router)

# 6. Content Drafts, Approvals & Versioning
api_router.include_router(drafts.router)

# 7. Student Progress & Bookmarks
api_router.include_router(progress.router)
api_router.include_router(bookmarks.router)

# 8. Dashboards
api_router.include_router(dashboard.router)

# 9. Resource Analytics & Audit Logs
api_router.include_router(analytics.router)

# 10. Scheduled Background Jobs & Email
api_router.include_router(admin_jobs.router)

# 11. CSV/JSON Import/Export & Bulk Operations
api_router.include_router(admin_data.router)
