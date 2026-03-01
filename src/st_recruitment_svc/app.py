from fastapi import FastAPI

from st_recruitment_svc.routers import auth_router

# Create FastAPI app instance. Keep root_path default to avoid NameError.
app = FastAPI(debug=True)

# include routers
app.include_router(auth_router)
