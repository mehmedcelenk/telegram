#!/usr/bin/env python3
"""Herkese açık Telegram kanallarındaki duyuruları izler."""
import html
from html.parser import HTMLParser
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

DOSYA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state.json')
VARSAYILAN = {'channel': 'oyunsektoruetkinlikleri', 'day': 4, 'time': '15:00',
              'footer': 'Kaynak: OTG', 'last_id': 0, 'last_run': '', 'update_offset': 0}
GUNLER = ['pazartesi', 'salı', 'çarşamba', 'perşembe', 'cuma', 'cumartesi', 'pazar']
BOS_ETIKETLER = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


def kaydet(durum):
    gecici = DOSYA + '.tmp'
    with open(gecici, 'w', encoding='utf-8') as dosya:
        json.dump(durum, dosya, ensure_ascii=False, indent=2)
        dosya.write('\n')
    os.replace(gecici, DOSYA)


def yukle():
    if os.path.exists(DOSYA):
        with open(DOSYA, encoding='utf-8') as dosya:
            durum = {**VARSAYILAN, **json.load(dosya)}
    else:
        durum = VARSAYILAN.copy()
    if 'channels' not in durum:
        durum['channels'] = {durum['channel']: durum['last_id']}
    durum.pop('channel', None)
    durum.pop('last_id', None)
    kaydet(durum)
    return durum


def istek(url, veri=None):
    govde = urlencode(veri).encode('utf-8') if veri is not None else None
    talep = Request(url, data=govde, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urlopen(talep, timeout=30) as yanit:
            return yanit.read().decode('utf-8')
    except HTTPError as hata:
        kaynak = 'Telegram bot hizmeti' if 'api.telegram.org' in url else 'Kanal önizlemesi'
        raise RuntimeError(f'{kaynak} isteği başarısız oldu (durum kodu: {hata.code}).') from hata


def api(yontem, **veri):
    try:
        sonuc = json.loads(istek('https://api.telegram.org/bot' + TOKEN + '/' + yontem, veri))
    except RuntimeError as hata:
        raise RuntimeError(f'{yontem}: {hata}') from hata
    if not sonuc.get('ok'):
        raise RuntimeError('Telegram isteği başarısız oldu.')
    return sonuc.get('result')


def fotograf_gonder(adres, aciklama=None):
    talep = Request(adres, headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(talep, timeout=30) as yanit:
        resim = yanit.read(10 * 1024 * 1024 + 1)
    if len(resim) > 10 * 1024 * 1024:
        raise RuntimeError('Fotoğraf izin verilen boyutu aşıyor.')
    sinir = '----TelegramDuyuruSiniri' + os.urandom(16).hex()
    govde = bytearray()
    alanlar = {'chat_id': SOHBET}
    if aciklama is not None:
        alanlar['caption'] = aciklama
    for ad, deger in alanlar.items():
        govde.extend(f'--{sinir}\r\nContent-Disposition: form-data; name="{ad}"\r\n\r\n{deger}\r\n'.encode('utf-8'))
    govde.extend(f'--{sinir}\r\nContent-Disposition: form-data; name="photo"; filename="duyuru.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode())
    govde.extend(resim)
    govde.extend(f'\r\n--{sinir}--\r\n'.encode())
    talep = Request('https://api.telegram.org/bot' + TOKEN + '/sendPhoto', data=bytes(govde),
                    headers={'Content-Type': 'multipart/form-data; boundary=' + sinir})
    try:
        with urlopen(talep, timeout=30) as yanit:
            sonuc = json.loads(yanit.read().decode('utf-8'))
    except HTTPError as hata:
        raise RuntimeError(f'Fotoğraf gönderilemedi (durum kodu: {hata.code}).') from hata
    if not sonuc.get('ok'):
        raise RuntimeError('Fotoğraf gönderilemedi.')


def mesaj(metin):
    # Telegram metin sınırını aşmadan bütün içeriği ilet.
    while metin:
        sinir = 0
        birim = 0
        for karakter in metin:
            uzunluk = len(karakter.encode('utf-16-le')) // 2
            if birim + uzunluk > 4096:
                break
            birim += uzunluk
            sinir += 1
        parca = metin[:sinir]
        if len(metin) > sinir:
            bolum = parca.rfind('\n')
            if bolum > 2048:
                parca = parca[:bolum + 1]
        api('sendMessage', chat_id=SOHBET, text=parca)
        metin = metin[len(parca):]


class DuyuruCozumleyici(HTMLParser):
    def __init__(self, kanal):
        super().__init__(convert_charrefs=True)
        self.kanal = kanal.lower()
        self.duyurular = []
        self.derinlik = 0
        self.mesaj_derinligi = None
        self.metin_derinligi = None
        self.aktif = None
        self.baglantilar = []

    def handle_starttag(self, etiket, nitelikler):
        attrs = dict(nitelikler)
        if etiket not in BOS_ETIKETLER:
            self.derinlik += 1
        siniflar = attrs.get('class', '').split()
        if self.aktif is None and 'tgme_widget_message' in siniflar:
            eslesme = re.fullmatch(r'([^/]+)/(\d+)', attrs.get('data-post', ''))
            if eslesme and eslesme.group(1).lower() == self.kanal:
                self.aktif = {'id': int(eslesme.group(2)), 'text': '', 'photo': ''}
                self.mesaj_derinligi = self.derinlik
        if self.aktif is None:
            return
        if 'tgme_widget_message_photo_wrap' in siniflar and not self.aktif['photo']:
            eslesme = re.search(r'background-image\s*:\s*url\(\s*[\'"]?(.*?)[\'"]?\s*\)', attrs.get('style', ''), re.I)
            if eslesme:
                self.aktif['photo'] = html.unescape(eslesme.group(1))
        if 'tgme_widget_message_text' in siniflar:
            self.metin_derinligi = self.derinlik
        elif self.metin_derinligi is not None:
            if etiket == 'br':
                self.aktif['text'] += '\n'
            elif etiket == 'img':
                self.aktif['text'] += attrs.get('alt', '')
            elif etiket == 'a':
                self.baglantilar.append((self.derinlik, attrs.get('href', ''), len(self.aktif['text'])))

    def handle_startendtag(self, etiket, nitelikler):
        self.handle_starttag(etiket, nitelikler)
        if etiket not in BOS_ETIKETLER:
            self.handle_endtag(etiket)

    def handle_data(self, veri):
        if self.aktif is not None and self.metin_derinligi is not None:
            self.aktif['text'] += veri

    def handle_endtag(self, etiket):
        if etiket in BOS_ETIKETLER:
            return
        if self.aktif is not None:
            if etiket == 'a' and self.baglantilar and self.baglantilar[-1][0] == self.derinlik:
                _, adres, baslangic = self.baglantilar.pop()
                gorunen = self.aktif['text'][baslangic:]
                if adres and adres != gorunen:
                    self.aktif['text'] += ' (' + adres + ')'
            if self.metin_derinligi == self.derinlik:
                self.metin_derinligi = None
            if self.mesaj_derinligi == self.derinlik:
                self.aktif['text'] = self.aktif['text'].strip()
                self.duyurular.append(self.aktif)
                self.aktif = None
                self.mesaj_derinligi = None
                self.baglantilar.clear()
        self.derinlik = max(0, self.derinlik - 1)


def kanal_tara(durum, kanal):
    sayfa = istek('https://t.me/s/' + kanal)
    cozumleyici = DuyuruCozumleyici(kanal)
    cozumleyici.feed(sayfa)
    duyurular = sorted(cozumleyici.duyurular, key=lambda duyuru: duyuru['id'])
    yeniler = [d for d in duyurular if d['id'] > durum['channels'][kanal]]
    if durum['channels'][kanal] == 0:
        yeniler = yeniler[-1:]
    for duyuru in yeniler:
        metin = (duyuru['text'] + '\n\n' + durum['footer']).strip()
        if duyuru['photo']:
            if len(metin.encode('utf-16-le')) // 2 <= 1024:
                fotograf_gonder(duyuru['photo'], metin)
            else:
                fotograf_gonder(duyuru['photo'])
                mesaj(metin)
        else:
            mesaj(metin)
        durum['channels'][kanal] = duyuru['id']
        kaydet(durum)
    return len(yeniler)


def tara(durum):
    return sum(kanal_tara(durum, kanal) for kanal in list(durum['channels']))


def panel(durum):
    gun = 'her gün' if durum['day'] == -1 else GUNLER[durum['day']]
    kanallar = ', '.join('@' + kanal for kanal in durum['channels']) or 'Yok'
    mesaj('📋 Kontrol paneli\n\n'
          f"Takip edilen kanallar: {kanallar}\nGönderim günü: {gun}\n"
          f"Gönderim saati: {durum['time']} (Türkiye saati)\nAltbilgi imzası: {durum['footer']}\n\n"
          'Komutlar\n/yardım — Bu paneli gösterir.\n/başlat — Bu paneli gösterir.\n'
          '/şimdi — Yeni duyuruları hemen tarar.\n/kanal <kanal adı> — Kanal ekler.\n'
          '/çıkar <kanal adı> — Kanalı takipten çıkarır.\n/kanallar — Kanalları gösterir.\n'
          '/gün <gün adı> — Gönderim gününü değiştirir.\n'
          '/saat <SS:DD> — Gönderim saatini değiştirir.\n/imza <metin> — Altbilgi imzasını değiştirir.')


def komut_isle(metin, durum):
    parcalar = metin.strip().split(maxsplit=1)
    if not parcalar or not parcalar[0].startswith('/'):
        return
    komut = parcalar[0].split('@', 1)[0].lower()
    deger = parcalar[1].strip() if len(parcalar) > 1 else ''
    if komut in ('/yardım', '/yardim', '/başlat', '/baslat'):
        panel(durum)
    elif komut in ('/şimdi', '/simdi'):
        if not tara(durum):
            mesaj('Yeni duyuru bulunamadı, kanal güncel.')
    elif komut == '/kanallar':
        mesaj('Takip edilen kanallar:\n' + ('\n'.join('@' + kanal for kanal in durum['channels']) or 'Henüz kanal eklenmedi.'))
    elif komut in ('/kanal', '/çıkar', '/cikar'):
        kanal = deger.strip()
        if '://' in kanal:
            adres = urlparse(kanal)
            if adres.scheme not in ('http', 'https') or adres.netloc.lower() not in ('t.me', 'www.t.me'):
                mesaj('Geçerli bir herkese açık kanal adı girin.')
                return
            kanal = adres.path.strip('/')
            if kanal.startswith('s/'):
                kanal = kanal[2:]
        kanal = kanal.lstrip('@')
        if not re.fullmatch(r'[A-Za-z0-9_]{5,32}', kanal):
            mesaj('Geçerli bir herkese açık kanal adı girin.')
            return
        mevcut = next((ad for ad in durum['channels'] if ad.lower() == kanal.lower()), None)
        if komut == '/kanal':
            if mevcut:
                mesaj(f'Bu kanal zaten takip ediliyor: @{mevcut}')
                return
            durum['channels'][kanal] = 0
            kaydet(durum)
            mesaj(f'Kanal takibe eklendi: @{kanal}')
        else:
            if not mevcut:
                mesaj(f'Bu kanal takip edilmiyor: @{kanal}')
                return
            del durum['channels'][mevcut]
            kaydet(durum)
            mesaj(f'Kanal takipten çıkarıldı: @{mevcut}')
    elif komut in ('/gün', '/gun'):
        gun = deger.lower()
        ceviri = {'sali': 'salı', 'carsamba': 'çarşamba', 'persembe': 'perşembe', 'hergun': 'hergün', 'her gün': 'hergün'}
        gun = ceviri.get(gun, gun)
        if gun not in GUNLER and gun != 'hergün':
            mesaj('Geçerli bir gün girin: pazartesi, salı, çarşamba, perşembe, cuma, cumartesi, pazar veya hergün.')
            return
        durum['day'] = -1 if gun == 'hergün' else GUNLER.index(gun)
        kaydet(durum)
        mesaj(f'Gönderim günü güncellendi: {"her gün" if gun == "hergün" else gun}')
    elif komut == '/saat':
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', deger):
            mesaj('Saati SS:DD biçiminde girin. Örnek: 15:00')
            return
        durum['time'] = deger
        kaydet(durum)
        mesaj(f'Gönderim saati güncellendi: {deger} (Türkiye saati)')
    elif komut == '/imza':
        if not deger:
            mesaj('Lütfen bir imza metni girin.')
            return
        durum['footer'] = deger
        kaydet(durum)
        mesaj('Altbilgi imzası güncellendi.')


def main():
    global TOKEN, SOHBET
    durum = yukle()
    TOKEN = os.environ.get('BOT_TOKEN', '').strip()
    SOHBET = os.environ.get('CHAT_ID', '').strip()
    if not TOKEN or not SOHBET:
        raise RuntimeError('Bot anahtarı ve sohbet kimliği ortam değişkenlerinde belirtilmelidir.')
    while True:
        guncellemeler = api('getUpdates', offset=durum['update_offset'], timeout=0, limit=100)
        if not guncellemeler:
            break
        for guncelleme in guncellemeler:
            ileti = guncelleme.get('message', {})
            if str(ileti.get('chat', {}).get('id', '')) == SOHBET and isinstance(ileti.get('text'), str):
                komut_isle(ileti['text'], durum)
            durum['update_offset'] = guncelleme['update_id'] + 1
            kaydet(durum)
    simdi = datetime.now(timezone(timedelta(hours=3)))
    bugun = simdi.date().isoformat()
    if (durum['day'] in (-1, simdi.weekday()) and simdi.strftime('%H:%M') >= durum['time']
            and durum['last_run'] != bugun):
        tara(durum)
        durum['last_run'] = bugun
    kaydet(durum)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, URLError) as hata:
        print(f'İşlem tamamlanamadı: {hata if isinstance(hata, RuntimeError) else type(hata).__name__}', file=sys.stderr)
        sys.exit(1)
