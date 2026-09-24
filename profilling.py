"""
profilling.py
--------------
Modul ini berisi logic untuk REQ FERDI:
1. Profile check -> ngecek pengeluaran bulan ini dibanding rata-rata historis
2. Sistem alert -> kasih status + pesan sesuai persentase dari baseline

Konsep (sesuai keputusan):
    x = rata-rata TOTAL pengeluaran dari bulan ke-1 sampai bulan ke-(n-1)
        x = total_pengeluaran(bulan_1, bulan_2, ..., bulan_n-1) / (n-1)
    y = total pengeluaran di bulan ke-n (bulan yang sedang berjalan, SEJAUH INI)

    persen = (y / x) * 100

    Kalau x >= y (alias persen <= 100%) -> masih di bawah/sama dengan
    rata-rata biasanya -> AMAN.
    Kalau x < y (alias persen > 100%)   -> udah lewat rata-rata biasanya
    -> WARNING.

Modul ini TIDAK import Transaction dari main.py secara langsung (biar
gak circular import). Sebagai gantinya, fungsi-fungsi di sini menerima
"transaction_model" sebagai parameter -- yaitu class Transaction (Beanie
Document) yang dikirim dari main.py saat fungsi dipanggil.
"""

from datetime import datetime
import random


def awal_bulan_dan_bulan_depan(tanggal: datetime):
    """
    Dari sebuah tanggal, hitung:
    - awal bulan yang sama (tanggal 1, jam 00:00:00)
    - awal bulan SETELAHNYA (buat batas atas query "$lt")
    Dipisah jadi fungsi sendiri karena dipakai di beberapa tempat.
    """
    awal_bulan_ini = tanggal.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if awal_bulan_ini.month == 12:
        awal_bulan_depan = awal_bulan_ini.replace(year=awal_bulan_ini.year + 1, month=1)
    else:
        awal_bulan_depan = awal_bulan_ini.replace(month=awal_bulan_ini.month + 1)

    return awal_bulan_ini, awal_bulan_depan


async def hitung_baseline_x(transaction_model, awal_bulan_ini: datetime):
    """
    Hitung x = rata-rata total pengeluaran ("purchase") dari SEMUA bulan
    SEBELUM bulan yang sedang berjalan.

    Return None kalau belum ada histori bulan sebelumnya sama sekali
    (misal ini baru bulan pertama Ferdi pakai app -- belum ada pembanding).
    """
    pipeline = [
        # ambil transaksi PEMBELIAN yang tanggalnya SEBELUM bulan ini
        {"$match": {
            "trx_type": "purchase",
            "date": {"$lt": awal_bulan_ini}
        }},
        # kelompokkan per tahun+bulan, supaya dapat total tiap bulan (bulan_1, bulan_2, dst)
        {"$group": {
            "_id": {"year": {"$year": "$date"}, "month": {"$month": "$date"}},
            "total_bulan": {"$sum": "$amount"}
        }}
    ]
    hasil = await transaction_model.aggregate(pipeline).to_list()

    if not hasil:
        return None  # belum ada bulan sebelumnya -> x belum bisa dihitung

    total_semua_bulan = sum(baris["total_bulan"] for baris in hasil)
    jumlah_bulan = len(hasil)  # ini representasi dari (n-1)

    return total_semua_bulan / jumlah_bulan


async def hitung_pengeluaran_bulan_ini(transaction_model, awal_bulan_ini: datetime, awal_bulan_depan: datetime):
    """
    Hitung y = total pengeluaran ("purchase") di bulan yang sedang berjalan,
    SEJAUH tanggal terakhir yang tercatat (bukan proyeksi sebulan penuh).
    """
    pipeline = [
        {"$match": {
            "trx_type": "purchase",
            "date": {"$gte": awal_bulan_ini, "$lt": awal_bulan_depan}
        }},
        {"$group": {"_id": None, "total_bulan": {"$sum": "$amount"}}}
    ]
    hasil = await transaction_model.aggregate(pipeline).to_list()

    return hasil[0]["total_bulan"] if hasil else 0


def tentukan_status_alert(x, y: int) -> dict:
    """
    Bandingkan y terhadap x, lalu tentukan status + pesan.

    Dibagi jadi beberapa tier (bukan cuma aman/warning) supaya user
    dapat sinyal yang lebih halus soal "sejauh ini" pengeluarannya
    ada di mana dibanding kebiasaan bulanannya:
        < 50%   -> aman
        50-80%  -> waspada_ringan
        80-100% -> waspada
        >=100%  -> lewat_batas (ini yang sama dengan kondisi x < y)
    """
    if x is None or x == 0:
        # belum ada baseline sama sekali -- gak bisa dihitung persentasenya
        return {
            "persen_dari_baseline": None,
            "status": "belum_ada_baseline",
            "message": (
                "Belum ada cukup histori bulan sebelumnya buat jadi pembanding. "
                "Catat terus transaksinya ya, biar makin akurat ke depannya!"
            )
        }

    persen = (y / x) * 100

    if persen < 50:
        status = "aman"
        pilihan_pesan = [
            "Pengeluaran bulan ini masih {p}% dari rata-rata biasanya. Aman terkendali!",
            "Santai, kamu masih jauh di bawah rata-rata pengeluaran bulananmu ({p}%). Lanjutkan!",
        ]
    elif persen < 80:
        status = "waspada_ringan"
        pilihan_pesan = [
            "Udah {p}% dari rata-rata bulananmu. Masih oke, tapi mulai diperhatiin ya!",
            "Pengeluaran bulan ini {p}% dari biasanya -- masih wajar, tetap dipantau ya!",
        ]
    elif persen < 100:
        status = "waspada"
        pilihan_pesan = [
            "Hati-hati, udah {p}% dari rata-rata pengeluaran bulananmu. Yuk direm dikit!",
            "Pengeluaran udah mendekati batas biasanya ({p}%). Semangat lebih hemat di sisa bulan ini!",
        ]
    else:
        status = "lewat_batas"
        pilihan_pesan = [
            "Pengeluaran bulan ini udah lewat rata-rata biasanya ({p}%)! Yuk evaluasi, bulan depan pasti lebih baik.",
            "Waduh, {p}% dari rata-rata bulanan udah terlampaui. Gak apa-apa, ini jadi bahan belajar buat bulan depan!",
        ]

    pesan_terpilih = random.choice(pilihan_pesan).format(p=round(persen, 1))

    return {
        "persen_dari_baseline": round(persen, 1),
        "status": status,
        "message": pesan_terpilih
    }


async def cek_profile_dan_alert(transaction_model, tanggal_acuan: datetime) -> dict:
    """
    Fungsi UTAMA yang dipanggil dari main.py.

    tanggal_acuan = tanggal transaksi (kalau dipanggil pas nambah transaksi)
                    atau datetime.now() (kalau dipanggil on-demand lewat
                    endpoint /transaction/alert).

    Return dict berisi baseline (x), pengeluaran bulan ini (y), persentase,
    status, dan pesan yang sudah jadi.
    """
    awal_bulan_ini, awal_bulan_depan = awal_bulan_dan_bulan_depan(tanggal_acuan)

    x = await hitung_baseline_x(transaction_model, awal_bulan_ini)
    y = await hitung_pengeluaran_bulan_ini(transaction_model, awal_bulan_ini, awal_bulan_depan)

    hasil = tentukan_status_alert(x, y)
    hasil["baseline_x_rata_rata_bulan_sebelumnya"] = round(x) if x is not None else None
    hasil["pengeluaran_bulan_ini_y"] = y

    return hasil