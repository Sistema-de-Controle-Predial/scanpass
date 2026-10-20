import pandas as pd
####
import qrcode
import json
from qrcode.constants import ERROR_CORRECT_H

def generate_qrcode(nome: str, numero_apartamento: str, tipo_carro: str):
    file_path = "qrcode.png"

    data = {
        "nome": nome,
        "numero_apartamento": numero_apartamento,
        "tipo_carro": tipo_carro,
    }
    qr = qrcode.QRCode(
        version=None,                 
        error_correction=ERROR_CORRECT_H,
        box_size=10,
        border=4,                     
    )
    qr.add_data(json.dumps(data, ensure_ascii=False))
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img.save(file_path)

    print("Codigo QR gerado!")

generate_qrcode(
    nome="João Silva",
    numero_apartamento="302",
    tipo_carro="Sedan",
)