from fastapi import FastAPI

app = FastAPI(debug=True, root_path=st_recruitment_svc)

# add routers