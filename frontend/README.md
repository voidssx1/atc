# ATC DSA Hub — React workspace

React 19 + TypeScript + Vite frontend for the Alan Turing Club DSA Hub.

## Local setup

Run npm ci, then copy .env.example to .env.local.

Set these values in frontend/.env.local:

- VITE_API_BASE_URL=http://localhost:8000/api/v1
- VITE_CLERK_PUBLISHABLE_KEY=pk_test_... from your Clerk application.

Start the frontend with npm run dev.

The FastAPI backend must be running locally at http://localhost:8000 with CORS allowing http://localhost:5173.

## Authentication and API

- src/auth.tsx loads the official ClerkJS and Clerk UI bundles from the Frontend API domain encoded in the publishable key, then exposes authentication state through a React context.
- Clerk session JWTs are exchanged at POST /api/v1/auth/clerk/exchange.
- The opaque application session token is held in memory only; it is never written to localStorage or sessionStorage.
- src/lib/api.ts sends bearer tokens, refreshes the backend session after an eligible 401, and retries the request once.
- src/App.tsx contains the student workspace, admin workspace, and community pages.
- The original club landing page is preserved in src/LandingPage.tsx.

## Student workspace

Dashboard, DSA roadmap/progress, learning resources, bookmarks, profile, events, projects and gallery.

## Admin workspace

Admin metrics, user role/status management, roadmap/resource draft submission and review, plus event/project/gallery creation, publishing and archiving.

## Commands

npm run lint
npm run build
npm run preview

Cloudflare Pages deployment uses the final branch, the frontend/ directory, and the dist/ build output. Configure VITE_API_BASE_URL and VITE_CLERK_PUBLISHABLE_KEY as GitHub Actions repository variables.
