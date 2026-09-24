"""
main.py -- SERVICE PROFILLING
------------------------------
Ini FastAPI app TERPISAH dari service "transaction". Tugasnya cuma 1:
kasih endpoint buat profile check & sistem alert (REQ FERDI).

Service ini TIDAK punya endpoint buat nambah/edit/hapus transaksi --
itu semua tetap di service "transaction" yang lain. Service ini
cuma BACA data dari collection MongoDB yang SAMA (trx_collection),
yang diisi oleh service "transaction".

PENTING: karena dua service beda, tapi harus baca database yang sama,
pastikan .env di folder INI punya MONGO_HOST, MONGO_DB_NAME (dan
MONGO_USER/MONGO_PASSWORD kalau pakai Atlas) yang SAMA PERSIS dengan
.env di folder "transaction". Kalau beda, service ini gak akan
nemu data apa-apa (nyambung ke database yang salah).
"""

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request
from datetime import datetime
from beanie import Document, init_beanie
from pymongo import AsyncMongoClient
from dotenv import load_dotenv
from typing import Optional
import os

# modul logic profile check & alert (file profilling.py, folder yang sama)
from profilling import cek_profile_dan_alert


app = FastAPI(title="Profilling Service")
load_dotenv()

FORMAT_DATE = "%Y-%m-%d"  # contoh: 2026-09-20


# Model Transaction di sini SENGAJA dibuat ulang (bukan import dari
# service "transaction", karena memang beda aplikasi/deployment).
# Field-nya harus SAMA persis dengan yang di service "transaction",
# dan nama collection-nya ("trx_collection") juga harus SAMA, supaya
# Beanie mapping ke data yang benar.
class Transaction(Document):
    date: datetime
    amount: int
    method: str
    desc: str
    trx_type: str

    class Settings:
        name = "trx_collection"  # HARUS SAMA dengan service "transaction"


def validasi_date(value: str) -> str:
    """Format tanggal harus YYYY-MM-DD."""
    try:
        datetime.strptime(value, FORMAT_DATE)
    except ValueError:
        raise ValueError(f"Format tanggal harus YYYY-MM-DD, contoh: 2026-09-20")
    return value


# Ubah pesan error Pydantic (teknis) jadi pesan simpel bahasa Indonesia
# (sama seperti yang ada di service "transaction", biar konsisten)
@app.exception_handler(RequestValidationError)
async def error_ramah_untuk_user(request: Request, exc: RequestValidationError):
    pesan_pesan = []
    for error in exc.errors():
        pesan = error["msg"]
        if pesan.startswith("Value error, "):
            pesan = pesan[len("Value error, "):]
        pesan_pesan.append(pesan)
    return JSONResponse(status_code=422, content={"pesan_error": pesan_pesan})


@app.on_event("startup")
async def init_db():
    mongo_user = os.environ.get("MONGO_USER", "")
    mongo_password = os.environ.get("MONGO_PASSWORD", "")
    mongo_host = os.environ.get("MONGO_HOST", "localhost:27017")
    mongo_db_name = os.environ.get("MONGO_DB_NAME", "bootcamp")

    if mongo_user and mongo_password:
        # Kalau user & password ADA -> susun URI ke Atlas
        mongo_uri = f"mongodb+srv://{mongo_user}:{mongo_password}@{mongo_host}/"
    else:
        # Kalau user/password TIDAK di-set -> fallback ke Mongo lokal tanpa auth
        mongo_uri = f"mongodb://{mongo_host}"

    client = AsyncMongoClient(mongo_uri)
    await init_beanie(database=client[mongo_db_name], document_models=[Transaction])


# --- SATU-SATUNYA ENDPOINT DI SERVICE INI ---
# Cek status profile check & alert. Bisa dipanggil kapan aja (on-demand),
# gak perlu nunggu ada transaksi baru masuk.
@app.get("/profilling/alert")
async def cek_alert(tanggal: Optional[str] = None):
    """
    Query parameter "tanggal" (format YYYY-MM-DD) OPSIONAL --
    kalau diisi, cek posisi keuangan di tanggal itu (misal mau cek
    ulang kondisi di tanggal tertentu). Kalau tidak diisi, otomatis
    pakai tanggal hari ini (real-time).
    """
    if tanggal:
        try:
            validasi_date(tanggal)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        tanggal_acuan = datetime.strptime(tanggal, FORMAT_DATE)
    else:
        tanggal_acuan = datetime.now()

    return await cek_profile_dan_alert(Transaction, tanggal_acuan)