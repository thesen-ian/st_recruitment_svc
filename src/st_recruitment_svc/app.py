from fastapi import FastAPI

# Create FastAPI app instance. Keep root_path default to avoid NameError.
app = FastAPI(debug=True)

# add routers
