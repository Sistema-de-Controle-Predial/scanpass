"""
leitor_qrcode.py  (enxuto + filtros + ZOOM DIGITAL ANTES DE LOCALIZAR)

Instalação:
    pip install opencv-python zxing-cpp

Teste:
    python leitor_qrcode.py [indice_ou_url]      ('q' na janela para sair)

Cada frame processado segue estes passos (para no primeiro que ler):
  1) DIRETA   : zxing na ROI, 1x, sem filtro.
  2) LOCALIZAR: o OpenCV acha candidatos na ROI (1x); recorta, amplia e lê com filtros.
  3) JANELAS  : ZOOM ANTES DE LOCALIZAR. Em rodízio, um nível por frame (1x,2x,3x,4x):
                a ROI NÃO é ampliada inteira. Escolhem-se poucas JANELAS de tamanho
                ROI/zoom nos pontos com "textura de QR Code" (mapa de textura) e na
                última posição conhecida; cada janela é ampliada pelo zoom e então
                lida (zxing) e localizada (OpenCV). Custo por janela é constante:
                uma janela ROI/4 ampliada 4x tem o tamanho da ROI.
Contornos: VERDE = lido | VERMELHO fino = candidato sem leitura |
           AMARELO fino = janelas de zoom examinadas neste frame.
"""

import time
import cv2
import numpy as np
import zxingcpp

_CLAHE = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))


def _gamma(gray, valor):
    lut = (np.linspace(0, 1, 256) ** valor * 255).astype(np.uint8)
    return cv2.LUT(gray, lut)


# nome -> função(gray) -> gray
FILTROS = {
    "contraste": lambda g: _CLAHE.apply(g),                    # luz irregular / sombra
    "clarear": lambda g: _CLAHE.apply(_gamma(g, 0.5)),         # noite / vidro escuro
    "escurecer": lambda g: _CLAHE.apply(_gamma(g, 2.0)),       # sol forte / estouro
    "nitidez": lambda g: cv2.addWeighted(g, 2.0, cv2.GaussianBlur(g, (0, 0), 3), -1.0, 0),
    "binarizar": lambda g: cv2.adaptiveThreshold(              # iluminação muito desigual
        g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10),
}


def _redimensionar(img, fator):
    if fator == 1:
        return img
    return cv2.resize(img, None, fx=fator, fy=fator,
                      interpolation=cv2.INTER_CUBIC if fator > 1 else cv2.INTER_AREA)


def _criar_localizador():
    if hasattr(cv2, "QRCodeDetectorAruco"):
        return cv2.QRCodeDetectorAruco()
    return cv2.QRCodeDetector()


def _area(quad):
    return cv2.contourArea(quad.astype(np.float32))


class LeitorQRCode:
    def __init__(self, camera_index=0, intervalo_repeticao=3.0, mostrar_preview=True,
                 largura=1280, altura=720, processar_a_cada=2,
                 roi=(0.0, 0.0, 1.0, 1.0), filtros=None,
                 niveis_zoom=(1, 2, 3, 4), max_janelas=2, min_textura=12,
                 localizar_em_janelas=True, alvo_zoom=600, margem=0.3):
        """
        roi:          (x0, y0, x1, y1) em frações 0..1. Ex.: (0.15, 0.2, 0.85, 0.8)
        filtros:      nomes de FILTROS a usar (None = todos)
        niveis_zoom:  ampliações testadas em rodízio, um nível por frame (>= 1)
        max_janelas:  quantas janelas examinar por frame nos níveis > 1
        min_textura:  sensibilidade do mapa de textura (0-255). Menor = mais janelas
        localizar_em_janelas: usa também o localizador do OpenCV dentro das janelas
        alvo_zoom:    tamanho (px) para o qual o recorte de um candidato é ampliado
        margem:       folga ao redor do candidato (fração do tamanho dele)
        """
        self.camera_index = camera_index
        self.intervalo_repeticao = intervalo_repeticao
        self.mostrar_preview = mostrar_preview
        self.largura, self.altura = largura, altura
        self.processar_a_cada = max(1, processar_a_cada)
        self.roi = roi
        self.filtros = [f for f in (filtros or FILTROS) if f in FILTROS]
        self.niveis = tuple(max(1.0, float(z)) for z in niveis_zoom) or (1.0,)
        self.max_janelas = max(1, max_janelas)
        self.min_textura = min_textura
        self.localizar_em_janelas = localizar_em_janelas
        self.alvo_zoom = alvo_zoom
        self.margem = margem

        self.ultimo_filtro = None      # passo/filtro que fez a última leitura
        self.estatisticas = {}         # passo/filtro -> nº de leituras
        self.candidatos = []           # quadriláteros localizados (coords do frame)
        self.janelas = []              # janelas examinadas no último frame (coords do frame)
        self._localizador = _criar_localizador()
        self._i_zoom = self._i_filtro = self._n = 0
        self._memoria = None           # (x, y, nº_do_frame) da última posição, coords da ROI
        self._ox = self._oy = 0
        self._ultimas = {}
        self._rodando = False

    def parar(self):
        self._rodando = False

    # ---------- leitura ----------
    @staticmethod
    def _ler(img):
        try:
            res = zxingcpp.read_barcodes(img, formats=zxingcpp.BarcodeFormat.QRCode)
        except (TypeError, AttributeError):
            res = zxingcpp.read_barcodes(img)
        return [r for r in res if r.text]   # ignora leituras vazias

    def _localizar(self, img):
        """Quadriláteros de possíveis QR Codes (decodificados ou não)."""
        try:
            ok, pts = self._localizador.detectMulti(img)
        except cv2.error:
            return []
        if not ok or pts is None:
            return []
        return [np.asarray(p, dtype=float).reshape(-1, 2) for p in pts]

    def _registrar(self, nome, resultados, S, O):
        """Pontos da imagem lida -> coords do frame:  frame = ponto / S + O."""
        self.ultimo_filtro = nome
        self.estatisticas[nome] = self.estatisticas.get(nome, 0) + 1
        saida = []
        for r in resultados:
            p = r.position
            pts = np.array([[p.top_left.x, p.top_left.y], [p.top_right.x, p.top_right.y],
                            [p.bottom_right.x, p.bottom_right.y],
                            [p.bottom_left.x, p.bottom_left.y]], dtype=float)
            saida.append((r.text, (pts / S + O).astype(int)))
        return saida

    def _ler_candidato(self, img, quad, S0, O0, prefixo="zoom"):
        """Recorta ao redor do candidato, amplia e tenta ler (sem filtro e com filtros).
        'img' se relaciona ao frame por:  frame = img / S0 + O0."""
        ih, iw = img.shape
        xs, ys = quad[:, 0], quad[:, 1]
        m = self.margem * max(xs.max() - xs.min(), ys.max() - ys.min()) + 8
        cx0, cy0 = max(int(xs.min() - m), 0), max(int(ys.min() - m), 0)
        cx1, cy1 = min(int(xs.max() + m), iw), min(int(ys.max() + m), ih)
        recorte = img[cy0:cy1, cx0:cx1]
        if recorte.size == 0:
            return []

        zoom = float(np.clip(self.alvo_zoom / max(recorte.shape), 1.0, 6.0))
        base = _redimensionar(recorte, zoom)
        S, O = zoom * S0, O0 + np.array([cx0, cy0]) / S0
        for nome in [None] + self.filtros:
            res = self._ler(base if nome is None else FILTROS[nome](base))
            if res:
                return self._registrar(f"{prefixo}+{nome or 'original'}", res, S, O)
        return []

    # ---------- mapa de textura: onde "parece haver" QR Code ----------
    def _centros(self, gray, ww, wh):
        """Centros (x, y) das janelas: última posição + picos de textura de QR Code."""
        gh, gw = gray.shape
        centros = []
        if self._memoria and self._n - self._memoria[2] <= 15:
            centros.append((int(self._memoria[0]), int(self._memoria[1])))

        # QR Code = muitas bordas verticais E horizontais juntas, em área compacta
        borda_x = (cv2.convertScaleAbs(cv2.Sobel(gray, cv2.CV_16S, 1, 0)) > 80).astype(np.uint8) * 255
        borda_y = (cv2.convertScaleAbs(cv2.Sobel(gray, cv2.CV_16S, 0, 1)) > 80).astype(np.uint8) * 255
        k = int(np.clip(min(ww, wh) // 4, 16, 200))
        dens = np.minimum(cv2.blur(borda_x, (k, k)), cv2.blur(borda_y, (k, k))).astype(np.float32)

        while len(centros) < self.max_janelas:
            _, maximo, _, (px, py) = cv2.minMaxLoc(dens)
            if maximo < self.min_textura:
                break
            centros.append((px, py))
            dens[max(py - wh // 2, 0):py + wh // 2 + 1, max(px - ww // 2, 0):px + ww // 2 + 1] = 0
        return centros[:self.max_janelas]

    def _buscar_janelas(self, gray, z, filtro, O):
        """ZOOM ANTES DE LOCALIZAR: janelas ROI/z, ampliadas z vezes, lidas e localizadas."""
        gh, gw = gray.shape
        ww, wh = max(int(gw / z), 48), max(int(gh / z), 48)
        for cx, cy in self._centros(gray, ww, wh):
            wx = int(np.clip(cx - ww // 2, 0, max(gw - ww, 0)))
            wy = int(np.clip(cy - wh // 2, 0, max(gh - wh, 0)))
            self.janelas.append((int(O[0]) + wx, int(O[1]) + wy,
                                 int(O[0]) + wx + ww, int(O[1]) + wy + wh))

            up = _redimensionar(gray[wy:wy + wh, wx:wx + ww], z)
            S0, O0 = float(z), O + np.array([wx, wy])

            res = self._ler(up)                                   # zxing na janela ampliada
            if res:
                return self._registrar(f"{z:g}x", res, S0, O0)
            if filtro:
                res = self._ler(FILTROS[filtro](up))
                if res:
                    return self._registrar(f"{z:g}x+{filtro}", res, S0, O0)

            if self.localizar_em_janelas:                         # localizador na janela ampliada
                quads = sorted(self._localizar(up), key=_area, reverse=True)
                self.candidatos += [(q / S0 + O0).astype(int) for q in quads]
                for quad in quads[:2]:
                    lidos = self._ler_candidato(up, quad, S0, O0, prefixo=f"{z:g}x>zoom")
                    if lidos:
                        return lidos
        return []

    # ---------- detecção por frame ----------
    def _detectar(self, frame):
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = self.roi
        self._ox, self._oy = int(x0 * w), int(y0 * h)
        O = np.array([self._ox, self._oy], dtype=float)
        gray = cv2.cvtColor(frame[self._oy:int(y1 * h), self._ox:int(x1 * w)], cv2.COLOR_BGR2GRAY)

        self._n += 1
        self.janelas = []
        z = self.niveis[self._i_zoom % len(self.niveis)]
        self._i_zoom += 1
        filtro = self.filtros[self._i_filtro % len(self.filtros)] if self.filtros else None
        self._i_filtro += 1

        # 1) leitura direta
        res = self._ler(gray)
        if res:
            self.candidatos = []
            return self._registrar("original", res, 1.0, O)

        # 2) localizar na ROI (1x) -> recorte + zoom + filtros
        quads = sorted(self._localizar(gray), key=_area, reverse=True)
        self.candidatos = [(q + O).astype(int) for q in quads]
        if quads:
            c = quads[0].mean(axis=0)
            self._memoria = (c[0], c[1], self._n)
        for quad in quads[:3]:
            lidos = self._ler_candidato(gray, quad, 1.0, O)
            if lidos:
                return lidos

        # 3) zoom antes de localizar (nível da vez)
        if z == 1:
            if filtro:
                res = self._ler(FILTROS[filtro](gray))
                if res:
                    return self._registrar(f"1x+{filtro}", res, 1.0, O)
            return []
        return self._buscar_janelas(gray, z, filtro, O)

    def detectar(self, frame):
        """Retorna lista de (texto, pontos) do frame, nas coordenadas do frame."""
        lidos = self._detectar(frame)
        if lidos:
            c = lidos[0][1].mean(axis=0)
            self._memoria = (c[0] - self._ox, c[1] - self._oy, self._n)
        return lidos

    def _repetido(self, codigo):
        agora = time.time()
        if agora - self._ultimas.get(codigo, 0) < self.intervalo_repeticao:
            return True
        self._ultimas[codigo] = agora
        return False

    # ---------- câmera / vídeo ----------
    def _abrir_camera(self):
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            raise RuntimeError(f"Não foi possível abrir a fonte {self.camera_index!r}.")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.largura)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.altura)
        cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        print(f"Câmera aberta em {int(cap.get(3))}x{int(cap.get(4))}")
        return cap

    def _desenhar(self, frame, deteccoes):
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = self.roi
        if self.roi != (0.0, 0.0, 1.0, 1.0):   # área de leitura
            cv2.rectangle(frame, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)),
                          (255, 128, 0), 1)
        if not deteccoes:
            for (a, b, c, d) in self.janelas:   # janelas de zoom examinadas
                cv2.rectangle(frame, (a, b), (c, d), (0, 220, 220), 1)
            for q in self.candidatos:           # candidatos sem leitura
                cv2.polylines(frame, [q.reshape(-1, 1, 2)], True, (0, 0, 255), 1)
        for texto, pts in deteccoes:
            cv2.polylines(frame, [pts.reshape(-1, 1, 2)], True, (0, 200, 0), 3)
            cv2.putText(frame, texto[:40], (pts[0][0], max(pts[0][1] - 10, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 0), 2)

    def ler(self):
        """Gerador: 'yield' do texto de cada QR Code novo lido."""
        cap = self._abrir_camera()
        self._rodando = True
        contador, deteccoes = 0, []
        try:
            while self._rodando:
                ok, frame = cap.read()
                if not ok:
                    break
                if contador % self.processar_a_cada == 0:
                    deteccoes = self.detectar(frame)
                    for texto, _ in deteccoes:
                        if not self._repetido(texto):
                            yield texto
                contador += 1

                if self.mostrar_preview:
                    self._desenhar(frame, deteccoes)
                    cv2.imshow("Leitor QR Code (q para sair)", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                else:
                    cv2.waitKey(1)
        finally:
            cap.release()
            cv2.destroyAllWindows()
            self._rodando = False


if __name__ == "__main__":
    import sys
    fonte = 0
    if len(sys.argv) > 1:
        fonte = int(sys.argv[1]) if sys.argv[1].isdigit() else sys.argv[1]

    leitor = LeitorQRCode(camera_index=fonte)   # ex.: roi=(0.15, 0.2, 0.85, 0.8)
    print("Aguardando QR Code... ('q' na janela para sair)")
    for codigo in leitor.ler():
        print(f"[{time.strftime('%H:%M:%S')}] QR Code lido: {codigo}  (via: {leitor.ultimo_filtro})")
    print("Leituras por passo/filtro:", leitor.estatisticas or "nenhuma")