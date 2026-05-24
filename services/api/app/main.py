from fastapi import FastAPI


app = FastAPI(title="Amazra API", version="0.0.1")


@app.get("/api/v1/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/v1/products")
def list_products():
    return {"items": []}


@app.get("/api/v1/orders")
def list_orders():
    return {"items": []}


@app.get("/api/v1/users/me")
def get_profile():
    return {"status": "ok"}
