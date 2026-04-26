from fastapi import FastAPI
from test_api import router   

app = FastAPI()
app.include_router(router)