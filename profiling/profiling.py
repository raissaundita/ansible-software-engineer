"""
Modul ini berisi logic untuk REQ FERDI:
1. Profile check -> ngecek pengeluaran bulan ini dibanding rata-rata historis
2. Sistem alert -> kasih status + pesan sesuai persentase dari baseline
3. Insight tambahan
   - tanggal transaksi terakhir yang tercatat bulan ini (biar tau data "berhenti" di tanggal berapa)
   - rata-rata pengeluaran per hari bulan ini
   - perbandingan langsung ke bulan lalu (bulan n-1 aja, bukan rata-rata)
   - 3 transaksi terbesar bulan ini

Konsep utama:
    x = rata-rata TOTAL pengeluaran dari bulan ke-1 sampai bulan ke-(n-1)
    y = total pengeluaran di bulan ke-n (bulan yang sedang berjalan, SEJAUH INI)
    persen = (y / x) * 100
    x >= y (persen <= 100%) -> AMAN | x < y (persen > 100%) -> WARNING

Modul ini TIDAK import Transaction dari main.py secara langsung (biar
tidak circular import). Sebagai gantinya, fungsi-fungsi di sini menerima
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
    """
    awal_bulan_ini = tanggal.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if awal_bulan_ini.month == 12:
        awal_bulan_depan = awal_bulan_ini.replace(year=awal_bulan_ini.year + 1, month=1)
    else:
        awal_bulan_depan = awal_bulan_ini.replace(month=awal_bulan_ini.month + 1)

    return awal_bulan_ini, awal_bulan_depan


def awal_bulan_sebelumnya(awal_bulan_ini: datetime) -> datetime:
    """Dari awal bulan ke-n, hitung awal bulan ke-(n-1)."""
    if awal_bulan_ini.month == 1:
        return awal_bulan_ini.replace(year=awal_bulan_ini.year - 1, month=12)
    return awal_bulan_ini.replace(month=awal_bulan_ini.month - 1)


# ------------------------------------------------------------------
# Baseline (x)
async def hitung_baseline_x(transaction_model, awal_bulan_ini: datetime):
    """
    Hitung x = rata-rata total pengeluaran ("purchase") dari SEMUA bulan
    SEBELUM bulan yang sedang berjalan.

    Return: x (float), atau None kalau belum ada histori bulan sebelumnya
    sama sekali (misal baru bulan pertama Ferdi pakai app).
    """
    pipeline = [
        {"$match": {
            "trx_type": "purchase",
            "date": {"$lt": awal_bulan_ini}
        }},
        {"$group": {
            "_id": {"year": {"$year": "$date"}, "month": {"$month": "$date"}},
            "total_bulan": {"$sum": "$amount"}
        }}
    ]
    hasil = await transaction_model.aggregate(pipeline).to_list()

    if not hasil:
        return None

    total_semua_bulan = sum(baris["total_bulan"] for baris in hasil)
    jumlah_bulan = len(hasil)  # representasi (n-1)

    return total_semua_bulan / jumlah_bulan


# ------------------------------------------------------------------
# Insight pengeluaran bulan ini (y) yang lebih detail
async def hitung_detail_pengeluaran_bulan_ini(transaction_model, awal_bulan_ini: datetime, awal_bulan_depan: datetime):
    """
    Hitung y (total pengeluaran bulan ini) SEKALIGUS ambil tanggal
    transaksi terakhir yang tercatat -- 2 informasi ini diambil dalam
    1 query aggregate biar efisien.

    Return dict:
        {
            "total_pengeluaran": int,
            "tanggal_transaksi_terakhir": datetime | None,
            "rata_rata_per_hari": float | None
        }

    "rata_rata_per_hari" dihitung dari tanggal HARI TERAKHIR yang benar-benar
    ada transaksinya (bukan dari tanggal hari ini) -- soalnya kalau Ferdi
    lupa nyatet 3 hari, membagi ke "hari ini" malah bikin rata-ratanya
    kelihatan lebih kecil dari yang sebenarnya.
    """
    pipeline = [
        {"$match": {
            "trx_type": "purchase",
            "date": {"$gte": awal_bulan_ini, "$lt": awal_bulan_depan}
        }},
        {"$group": {
            "_id": None,
            "total_bulan": {"$sum": "$amount"},
            "tanggal_terakhir": {"$max": "$date"}
        }}
    ]
    hasil = await transaction_model.aggregate(pipeline).to_list()

    if not hasil:
        # belum ada transaksi PEMBELIAN sama sekali bulan ini
        return {
            "total_pengeluaran": 0,
            "tanggal_transaksi_terakhir": None,
            "rata_rata_per_hari": None
        }

    total_bulan = hasil[0]["total_bulan"]
    tanggal_terakhir = hasil[0]["tanggal_terakhir"]

    jumlah_hari_berjalan = tanggal_terakhir.day
    rata_rata_per_hari = total_bulan / jumlah_hari_berjalan

    return {
        "total_pengeluaran": total_bulan,
        "tanggal_transaksi_terakhir": tanggal_terakhir,
        "rata_rata_per_hari": rata_rata_per_hari
    }


async def hitung_pengeluaran_bulan_lalu(transaction_model, awal_bulan_ini: datetime):
    """
    Hitung total pengeluaran di TEPAT 1 bulan sebelumnya (bulan n-1 aja,
    BUKAN rata-rata semua histori seperti baseline x).
    Ini buat perbandingan yang lebih "kebayang" -- orang biasanya lebih
    gampang ngerti "naik/turun dari bulan lalu" dibanding rata-rata sekian bulan".

    Return int, atau None kalau bulan lalu belum ada data.
    """
    batas_bawah = awal_bulan_sebelumnya(awal_bulan_ini)
    batas_atas = awal_bulan_ini  # awal bulan ini = akhir dari bulan lalu

    pipeline = [
        {"$match": {
            "trx_type": "purchase",
            "date": {"$gte": batas_bawah, "$lt": batas_atas}
        }},
        {"$group": {"_id": None, "total_bulan": {"$sum": "$amount"}}}
    ]
    hasil = await transaction_model.aggregate(pipeline).to_list()

    return hasil[0]["total_bulan"] if hasil else None


async def ambil_top_transaksi_terbesar(transaction_model, awal_bulan_ini: datetime, awal_bulan_depan: datetime, jumlah: int = 3):
    """
    Ambil N transaksi PEMBELIAN dengan nominal terbesar di bulan ini,
    biar Ferdi langsung tau "boros-nya di mana", bukan cuma tau totalnya.
    """
    pipeline = [
        {"$match": {
            "trx_type": "purchase",
            "date": {"$gte": awal_bulan_ini, "$lt": awal_bulan_depan}
        }},
        {"$sort": {"amount": -1}},
        {"$limit": jumlah},
        {"$project": {"_id": 0, "date": 1, "amount": 1, "desc": 1, "method": 1}}
    ]
    return await transaction_model.aggregate(pipeline).to_list()


# ------------------------------------------------------------------
# Status alert
def tentukan_status_alert(x, y: int) -> dict:
    """
    Bandingkan y terhadap x, lalu tentukan status + pesan.
    Dibagi jadi beberapa tier supaya user dapat sinyal yang lebih halus
    soal "sejauh ini" posisinya ada di mana dibanding kebiasaan bulanan:
        < 50%   -> aman
        50-80%  -> waspada_ringan
        80-100% -> waspada
        >=100%  -> lewat_batas (sama dengan kondisi x < y)
    """
    if x is None or x == 0:
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
            "Pengeluaran bulan ini masih {p}% dari rata-rata biasanya. Aman terkendali boz!",
            "Santai, kamu masih jauh di bawah rata-rata pengeluaran bulananmu ({p}%). Aman ajaa!",
        ]
    elif persen < 80:
        status = "waspada_ringan"
        pilihan_pesan = [
            "Udah {p}% dari rata-rata bulananmu. Masih oke, tapi mulai diperhatiin ya!",
            "Pengeluaran bulan ini {p}% dari biasanya -- masih wajar si, tapi tetap dipantau ya!",
        ]
    elif persen < 100:
        status = "waspada"
        pilihan_pesan = [
            "Hati-hati, udah {p}% dari rata-rata pengeluaran bulananmu. Yuk direm dikit yuk!",
            "Pengeluaran udah mendekati batas biasanya ({p}%). Semangat lebih hemat di sisa bulan ini!",
        ]
    else:
        status = "lewat_batas"
        pilihan_pesan = [
            "Pengeluaran bulan ini udah lewat rata-rata biasanya ({p}%)! Gapapa namanya juga hidup, bulan depan pasti lebih baik!",
            "Waduh, {p}% dari rata-rata bulanan udah terlampaui. Gapapa, ini jadi bahan belajar buat bulan depan!",
        ]

    pesan_terpilih = random.choice(pilihan_pesan).format(p=round(persen, 1))

    return {
        "persen_dari_baseline": round(persen, 1),
        "status": status,
        "message": pesan_terpilih
    }


# ------------------------------------------------------------------
# Fungsi UTAMA -- gabungin semuanya jadi 1 response yang ringkas
async def cek_profile_dan_alert(transaction_model, tanggal_acuan: datetime) -> dict:
    """
    Fungsi UTAMA yang dipanggil dari main.py.

    tanggal_acuan = tanggal transaksi (kalau dipanggil pas nambah transaksi)
                    atau tanggal yang diinput user / datetime.now() (kalau
                    dipanggil on-demand lewat endpoint /transaction/alert).
    """
    awal_bulan_ini, awal_bulan_depan = awal_bulan_dan_bulan_depan(tanggal_acuan)

    x = await hitung_baseline_x(transaction_model, awal_bulan_ini)

    detail_bulan_ini = await hitung_detail_pengeluaran_bulan_ini(transaction_model, awal_bulan_ini, awal_bulan_depan)
    y = detail_bulan_ini["total_pengeluaran"]

    total_bulan_lalu = await hitung_pengeluaran_bulan_lalu(transaction_model, awal_bulan_ini)
    top_transaksi = await ambil_top_transaksi_terbesar(transaction_model, awal_bulan_ini, awal_bulan_depan)

    # hitung selisih & persen perubahan dibanding bulan lalu (kalau ada datanya)
    perbandingan_bulan_lalu = None
    if total_bulan_lalu is not None:
        selisih = y - total_bulan_lalu
        if total_bulan_lalu != 0:
            persen_perubahan = round((selisih / total_bulan_lalu) * 100, 1)
        else:
            persen_perubahan = None
        perbandingan_bulan_lalu = {
            "total_bulan_lalu": total_bulan_lalu,
            "selisih": selisih,  # positif = lebih boros dari bulan lalu, negatif = lebih hemat
            "persen_perubahan": persen_perubahan
        }

    # --- status alert (logic utama, gak berubah) ---
    hasil_alert = tentukan_status_alert(x, y)

    # --- gabungin jadi 1 response yang ringkas ---
    hasil_alert["baseline_x_rata_rata_bulan_sebelumnya"] = round(x) if x is not None else None
    hasil_alert["pengeluaran_bulan_ini_y"] = y

    hasil_alert["periode_bulan_ini"] = {
        "bulan": awal_bulan_ini.strftime("%Y-%m"),
        "data_tercatat_sampai_tanggal": (
            detail_bulan_ini["tanggal_transaksi_terakhir"].strftime("%Y-%m-%d")
            if detail_bulan_ini["tanggal_transaksi_terakhir"] else None
        )
    }

    hasil_alert["rata_rata_pengeluaran_per_hari_bulan_ini"] = (
        round(detail_bulan_ini["rata_rata_per_hari"])
        if detail_bulan_ini["rata_rata_per_hari"] is not None else None
    )

    hasil_alert["perbandingan_bulan_lalu"] = perbandingan_bulan_lalu

    hasil_alert["top_3_transaksi_terbesar_bulan_ini"] = [
        {
            "tanggal": trx["date"].strftime("%Y-%m-%d"),
            "amount": trx["amount"],
            "method": trx["method"],
            "desc": trx["desc"]
        }
        for trx in top_transaksi
    ]

    return hasil_alert