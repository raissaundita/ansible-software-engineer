from fastapi import FastAPI, UploadFile, File, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from datetime import datetime
from beanie import Document, init_beanie, PydanticObjectId
from pymongo import AsyncMongoClient
from enum import Enum
from typing import Optional
from dotenv import load_dotenv
import pandas as pd
import io
import os
import re

app = FastAPI(title="Transaction Service")
load_dotenv()
 
# TASK 1
class TrxType(str, Enum):
    income = "income"
    purchase = "purchase"
 
 
class Transaction(Document):
    date: datetime
    amount: int
    method: str
    desc: str
    trx_type: str
 
    class Settings:
        name = "trx_collection"
 

KATA_KE_TRX_TYPE = {
    "pemasukan": TrxType.income,
    "pembelian": TrxType.purchase,
}

METODE_YANG_VALID = ["cash", "gopay", "bca", "shopee", "mandiri"]
 
FORMAT_DATE = "%Y-%m-%d"  # contoh: 2026-09-20
 

def validasi_amount(value):
    """Amount harus bilangan bulat (diskrit), minimal 1, tanpa batas atas."""
    if isinstance(value, str):
        # buang semua karakter selain digit (Rp, titik, koma, spasi)
        angka_bersih = re.sub(r"[^\d]", "", value)
        if angka_bersih == "":
            raise ValueError("Jumlah uang tidak boleh kosong")
        value = int(angka_bersih)
 
    # cek diskrit: kalau user kirim angka desimal seperti 50000.5, tolak
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("Jumlah uang harus bilangan bulat (tidak boleh ada koma/desimal)")
 
    value = int(value)
    if value < 1:
        raise ValueError("Jumlah uang minimal Rp 1, dan tidak boleh negatif")
    return value
 
 
def validasi_trx_type(value):
    """Hanya menerima 'pemasukan' atau 'pembelian'."""
    kunci = str(value).strip().lower()
    if kunci not in KATA_KE_TRX_TYPE:
        raise ValueError("Jenis transaksi harus 'pemasukan' atau 'pembelian'")
    return KATA_KE_TRX_TYPE[kunci].value  # disimpan baku sebagai "income"/"purchase"
 
 
def validasi_method(value):
    """Method harus salah satu dari daftar METODE_YANG_VALID."""
    kunci = str(value).strip().lower()
    if kunci not in METODE_YANG_VALID:
        pilihan = ", ".join(METODE_YANG_VALID)
        raise ValueError(f"Metode pembayaran '{value}' tidak dikenali. Gunakan salah satu dari: {pilihan}")
    return kunci
 
 
def validasi_desc(value):
    if not str(value).strip():
        raise ValueError("Deskripsi transaksi tidak boleh kosong. Contoh: 'beli kopi'")
    return value
 
 
def validasi_date(value):
    """Date custom harus format YYYY-MM-DD (tanpa jam)."""
    try:
        datetime.strptime(value, FORMAT_DATE)
    except ValueError:
        raise ValueError(f"Format date harus YYYY-MM-DD, contoh: 2026-09-20")
    return value
 
 
class RequestNewTransaction(BaseModel):
    amount: int
    method: str
    desc: str
    trx_type: str
    date: Optional[str] = None  # opsional -- kalau kosong, dipakai waktu sekarang
 
    @field_validator("amount", mode="before")
    @classmethod
    def cek_amount(cls, value):
        return validasi_amount(value)
 
    @field_validator("trx_type", mode="before")
    @classmethod
    def cek_trx_type(cls, value):
        return validasi_trx_type(value)
 
    @field_validator("method", mode="before")
    @classmethod
    def cek_method(cls, value):
        return validasi_method(value)
 
    @field_validator("desc")
    @classmethod
    def cek_desc(cls, value):
        return validasi_desc(value)
 
    @field_validator("date")
    @classmethod
    def cek_date(cls, value):
        # String kosong ("") atau cuma spasi dianggap SAMA dengan tidak diisi,
        # supaya user tidak "dipaksa" isi date walau field-nya optional
        if value is None or value.strip() == "":
            return None
        return validasi_date(value)
 
 
class RequestEditTransaction(BaseModel):
    # Semua field OPSIONAL untuk edit -- user cuma kirim field yang
    # mau diubah saja, field lain biarkan kosong/tidak dikirim
    amount: Optional[int] = None
    method: Optional[str] = None
    desc: Optional[str] = None
    trx_type: Optional[str] = None
    date: Optional[str] = None
 
    @field_validator("amount", mode="before")
    @classmethod
    def cek_amount(cls, value):
        if value is None:
            return value
        return validasi_amount(value)
 
    @field_validator("trx_type", mode="before")
    @classmethod
    def cek_trx_type(cls, value):
        if value is None:
            return value
        return validasi_trx_type(value)
 
    @field_validator("method", mode="before")
    @classmethod
    def cek_method(cls, value):
        if value is None:
            return value
        return validasi_method(value)
 
    @field_validator("desc")
    @classmethod
    def cek_desc(cls, value):
        if value is None:
            return value
        return validasi_desc(value)
 
    @field_validator("date")
    @classmethod
    def cek_date(cls, value):
        if value is None or value.strip() == "":
            return None
        return validasi_date(value)
 
 
# Ubah pesan error Pydantic (teknis) jadi pesan simpel bahasa Indonesia
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
 
 
@app.post("/transaction/add")
async def add_transaction(request_body: RequestNewTransaction):
    # Kalau user isi "date" custom (misal transaksi kemarin yang lupa
    # dicatat), pakai date itu. Kalau tidak diisi, pakai waktu SEKARANG.
    if request_body.date:
        date_transaksi = datetime.strptime(request_body.date, FORMAT_DATE)
    else:
        date_transaksi = datetime.now()
 
    trx = Transaction(
        date=date_transaksi,
        amount=request_body.amount,
        method=request_body.method,
        desc=request_body.desc,
        trx_type=request_body.trx_type
    )
    await trx.insert()
    return trx
 
 
@app.get("/transaction")
async def get_transaction(start_date: datetime, end_date: datetime):
    return await Transaction.find(
        Transaction.date >= start_date, Transaction.date <= end_date
    ).to_list()
 
 
# EDIT transaksi (kalau user salah input)
# Pakai PATCH (bukan PUT), karena endpoint ini memang didesain untuk
# ubah SEBAGIAN field saja -- user tidak wajib kirim semua field sekaligus
@app.patch("/transaction/{transaction_id}")
async def edit_transaction(transaction_id: PydanticObjectId, request_body: RequestEditTransaction):
    trx = await Transaction.get(transaction_id)
    if trx is None:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")
 
    # Ambil hanya field yang benar-benar dikirim user (yang tidak None)
    data_baru = request_body.model_dump(exclude_none=True)
 
    # "date" perlu ditangani khusus karena formatnya string YYYY-MM-DD,
    # sedangkan field di database ("date") bertipe datetime
    if "date" in data_baru:
        trx.date = datetime.strptime(data_baru.pop("date"), FORMAT_DATE)
 
    for nama_field, nilai_baru in data_baru.items():
        setattr(trx, nama_field, nilai_baru)
 
    await trx.save()
    return trx
 
 
#  DELETE transaksi (kalau user salah input & mau dihapus total)
@app.delete("/transaction/{transaction_id}")
async def delete_transaction(transaction_id: PydanticObjectId):
    trx = await Transaction.get(transaction_id)
    if trx is None:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")
    await trx.delete()
    return {"message": "Transaksi berhasil dihapus", "id": str(transaction_id)}
 
 
@app.get("/transaction/summary")
async def summary_by_method(year: int, month: int):
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
 
    pipeline = [
        {"$match": {"date": {"$gte": start, "$lt": end}}},
        {"$group": {
            "_id": "$trx_type",
            "total_amount": {"$sum": "$amount"},
            "count": {"$sum": 1},
        }}
    ]
    return await Transaction.aggregate(pipeline).to_list()

# TASK 2
@app.get("/transaction/summary/behavior")
async def summary_behavior(
    year: int,
    month: int,
    # Default 0.2 artinya: kalau user berhasil menabung >= 20% dari
    # incomenya bulan itu, dia dianggap "Big Saver".
    ambang_big_saver: float = 0.2
):
    # --- validasi kecil biar ambang_big_saver tidak aneh (misal negatif) ---
    if not (0 <= ambang_big_saver <= 1):
        raise HTTPException(
            status_code=422,
            detail="ambang_big_saver harus berupa persen antara 0 dan 1 (misal 0.2 untuk 20%)"
        )
 
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
 
    pipeline = [
        {"$match": {"date": {"$gte": start, "$lt": end}}},
        {"$group": {
            "_id": "$trx_type",
            "total_amount": {"$sum": "$amount"},
        }}
    ]
    hasil = await Transaction.aggregate(pipeline).to_list()
 
    total_income = 0
    total_purchase = 0
    for baris in hasil:
        if baris["_id"] == "income":
            total_income = baris["total_amount"]
        elif baris["_id"] == "purchase":
            total_purchase = baris["total_amount"]
 
    nett = total_income - total_purchase
 
    # Kalau income = 0 (belum ada pemasukan bulan itu), rasio tidak bisa
    # dihitung (pembagian dengan nol). Kita anggap kondisi ini otomatis
    # "Reckless Spender" kalau ada purchase, atau netral kalau semua kosong.
    if total_income == 0:
        if total_purchase > 0:
            label = "Reckless Spender"
        else:
            label = "Belum Ada Transaksi"
        savings_rate = None
    else:
        # savings_rate = seberapa besar persen income yang "tersisa" (ditabung)
        # Contoh: income 10jt, purchase 12jt -> nett = -2jt
        # savings_rate = -2jt / 10jt = -0.2 -> artinya minus 20% (boros)
        savings_rate = nett / total_income
 
        if savings_rate < 0:
            # Pengeluaran LEBIH BESAR dari pemasukan -> jelas boros
            label = "Reckless Spender"
        elif savings_rate >= ambang_big_saver:
            # Berhasil menyisihkan >= ambang_big_saver (default 20%)
            # dari pemasukannya sendiri -> dianggap rajin menabung
            label = "Big Saver"
        else:
            # Di antara 0% - ambang_big_saver -> tidak boros, tapi
            # tabungannya belum banyak juga
            label = "Cukup Terkendali"
 
    return {
        "year": year,
        "month": month,
        "total_income": total_income,
        "total_purchase": total_purchase,
        "nett": nett,
        # savings_rate dikirim juga ke user dalam bentuk persen biar
        # user tahu "kenapa" dia dapat label itu, bukan cuma dikasih
        # labelnya doang tanpa konteks
        "savings_rate_persen": round(savings_rate * 100, 1) if savings_rate is not None else None,
        "ambang_big_saver_dipakai_persen": round(ambang_big_saver * 100, 1),
        "behavior_label": label
    }

# TASK 3
KEMUNGKINAN_NAMA_KOLOM = {
    "amount": ["amount", "jumlah", "nominal", "total"],
    "method": ["method", "metode", "cara bayar", "payment method", "payment_method"],
    "desc": ["desc", "deskripsi", "keterangan", "catatan", "description"],
    "trx_type": ["trx_type", "jenis", "tipe", "jenis transaksi", "kategori"],
    "date": ["date", "tanggal", "waktu", "datetime"],
}
 
 
def cari_nama_kolom_asli(df, kemungkinan_nama):
    kolom_excel_lower = {kolom.strip().lower(): kolom for kolom in df.columns}
    for nama in kemungkinan_nama:
        if nama in kolom_excel_lower:
            return kolom_excel_lower[nama]
    return None
 
 
@app.post("/transaction/import-excel/preview")
async def preview_excel(file: UploadFile = File(...)):
    isi_file = await file.read()
    df = pd.read_excel(io.BytesIO(isi_file))
 
    kolom_terdeteksi = {}
    for field_kita, kemungkinan in KEMUNGKINAN_NAMA_KOLOM.items():
        kolom_terdeteksi[field_kita] = cari_nama_kolom_asli(df, kemungkinan)
 
    return {
        "nama_kolom_di_excel": list(df.columns),
        "jumlah_baris": len(df),
        "contoh_5_baris_pertama": df.head(5).to_dict(orient="records"),
        "kolom_yang_berhasil_dikenali": kolom_terdeteksi
    }
 
 
@app.post("/transaction/import-excel")
async def import_dari_excel(file: UploadFile = File(...)):
    isi_file = await file.read()
    df = pd.read_excel(io.BytesIO(isi_file))
 
    kolom_amount = cari_nama_kolom_asli(df, KEMUNGKINAN_NAMA_KOLOM["amount"])
    kolom_method = cari_nama_kolom_asli(df, KEMUNGKINAN_NAMA_KOLOM["method"])
    kolom_desc = cari_nama_kolom_asli(df, KEMUNGKINAN_NAMA_KOLOM["desc"])
    kolom_trx_type = cari_nama_kolom_asli(df, KEMUNGKINAN_NAMA_KOLOM["trx_type"])
    kolom_date = cari_nama_kolom_asli(df, KEMUNGKINAN_NAMA_KOLOM["date"])
 
    # trx_type TIDAK wajib ada -- kalau tidak ada kolomnya, kita tebak
    # dari tanda +/- nilai amount (lihat loop di bawah)
    kolom_wajib = {
        "amount": kolom_amount, "method": kolom_method, "desc": kolom_desc
    }
    kolom_hilang = [nama for nama, ditemukan in kolom_wajib.items() if ditemukan is None]
    if kolom_hilang:
        return {
            "error": f"Kolom berikut tidak ditemukan di file excel: {kolom_hilang}",
            "nama_kolom_yang_ada_di_excel": list(df.columns),
            "saran": "Coba endpoint /transaction/import-excel/preview dulu untuk cek nama kolom"
        }
 
    berhasil = []
    gagal = []
 
    for index, baris in df.iterrows():
        try:
            nilai_amount_asli = baris[kolom_amount]
 
            # Kalau kolom trx_type memang ada di excel, pakai itu.
            # Kalau tidak ada, TEBAK jenis transaksi dari tanda +/- amount:
            # negatif = pembelian (uang keluar), positif = pemasukan (uang masuk)
            if kolom_trx_type:
                trx_type_mentah = baris[kolom_trx_type]
            else:
                trx_type_mentah = "pemasukan" if nilai_amount_asli >= 0 else "pembelian"
 
            data_valid = RequestNewTransaction(
                amount=abs(nilai_amount_asli),  # disimpan positif -- arahnya sudah ada di trx_type
                method=baris[kolom_method],
                desc=baris[kolom_desc],
                trx_type=trx_type_mentah
            )
 
            # Buang bagian JAM dari datetime, cuma tanggalnya saja yang disimpan
            if kolom_date:
                tanggal_saja = pd.to_datetime(baris[kolom_date]).normalize()
            else:
                tanggal_saja = datetime.now()
 
            trx = Transaction(
                date=tanggal_saja,
                amount=data_valid.amount,
                method=data_valid.method,
                desc=data_valid.desc,
                trx_type=data_valid.trx_type
            )
            await trx.insert()
            berhasil.append(index + 2)
        except Exception as error:
            gagal.append({"baris_excel": index + 2, "error": str(error)})
 
    return {
        "total_baris": len(df),
        "berhasil_diimport": len(berhasil),
        "gagal_diimport": gagal
    }
 