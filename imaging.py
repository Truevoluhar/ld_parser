"""Strict odd/even pairing and bounded raster normalization, with no agent dependency."""
from dataclasses import dataclass
from pathlib import Path
import io
import re
import warnings

from PIL import Image, ImageOps
import pypdfium2 as pdfium

Image.MAX_IMAGE_PIXELS = 50_000_000
MAX_PAGES = 40
MAX_EDGE = 2400
MAX_JPEG_BYTES = 2 * 1024 * 1024


@dataclass
class Pair:
    first_page: int
    source_names: list[str]
    pdf: bytes
    sizes: list[list[int]]
    signature_png: bytes


def jpeg(image):
    image = ImageOps.exif_transpose(image)
    if image.mode in ('RGBA','LA') or (image.mode=='P' and 'transparency' in image.info):
        rgba = image.convert('RGBA')
        background = Image.new('RGBA',rgba.size,'white')
        image = Image.alpha_composite(background,rgba).convert('RGB')
    else:
        image = image.convert('RGB')
    image.thumbnail((MAX_EDGE,MAX_EDGE),Image.Resampling.LANCZOS)
    for edge in (MAX_EDGE,2000,1600):
        image.thumbnail((edge,edge),Image.Resampling.LANCZOS)
        out=io.BytesIO(); image.save(out,format='JPEG',quality=92,optimize=False)
        if out.tell() <= MAX_JPEG_BYTES:
            return image.copy(), list(image.size)
    raise ValueError('Stran je po zmanjšanju še vedno prevelika; pripravite manjši posnetek.')


def frames(name, data):
    ext=Path(name).suffix.lower()
    if ext=='.pdf':
        doc=pdfium.PdfDocument(data)
        try:
            if not 1 <= len(doc) <= MAX_PAGES:
                raise ValueError('PDF mora imeti od 1 do 40 strani.')
            for i in range(len(doc)):
                page=doc[i]
                try:
                    w,h=page.get_size()
                    if w<=0 or h<=0: raise ValueError('Neveljavne dimenzije PDF strani.')
                    scale=min(180/72,MAX_EDGE/max(w,h))
                    bitmap=page.render(scale=scale,rev_byteorder=True)
                    try:
                        yield bitmap.to_pil().copy()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        finally:
            doc.close()
    elif ext in {'.tif','.tiff','.png','.jpg','.jpeg'}:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                count=getattr(img,'n_frames',1)
                if count > MAX_PAGES: raise ValueError('Datoteka vsebuje preveč strani.')
                for i in range(count):
                    img.seek(i)
                    if img.width*img.height>Image.MAX_IMAGE_PIXELS: raise ValueError('Slika je prevelika.')
                    yield img.copy()
    else:
        raise ValueError('Podprti formati so TIFF, PDF, PNG in JPEG.')


def normalize(files: list[tuple[str, bytes]], first_page: int | None = None) -> list[Pair]:
    if not files: raise ValueError('Naložite najmanj en par strani.')
    if first_page is not None and (len(files)!=1 or first_page<1 or first_page%2!=1):
        raise ValueError('first_page je dovoljen samo za eno datoteko in mora biti pozitivna neparna številka.')
    pages={}
    for name,data in files:
        stem=Path(name.replace('\\','/')).stem
        if first_page is not None:
            start=first_page
        elif re.fullmatch(r'\d+',stem):
            start=int(stem)
        else:
            raise ValueError('Ime datoteke mora biti številka strani (npr. 00000187.tif); za en PDF uporabite first_page.')
        if start<1: raise ValueError('Številke strani morajo biti pozitivne.')
        count=0
        try:
            for index,img in enumerate(frames(name,data)):
                count+=1
                if len(pages)>=MAX_PAGES: raise ValueError('Na klic je dovoljenih največ 40 strani.')
                number=start+index
                if number in pages: raise ValueError(f'Stran {number} je podvojena.')
                normalized,size=jpeg(img)
                pages[number]=(normalized,name,size)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f'Datoteke {name} ni mogoče varno prebrati.') from exc
        if count==0: raise ValueError('Prazna datoteka.')
        if count>1 and (start%2==0 or count%2!=0):
            raise ValueError('Večstranski PDF/TIFF se mora začeti z neparno stranjo in vsebovati cele pare.')
    for number in sorted(pages):
        partner=number+1 if number%2 else number-1
        if partner not in pages:
            raise ValueError(f'Strani {number} manjka pripadajoča stran {partner}.')
    pairs=[]
    for number in sorted(n for n in pages if n%2):
        a,b=pages[number],pages[number+1]
        output=io.BytesIO()
        a[0].save(output,format='PDF',save_all=True,append_images=[b[0]],resolution=180,quality=92)
        if output.tell()>8*1024*1024: raise ValueError('Normalizirani par presega 8 MB.')
        width,height=b[0].size
        crop=b[0].crop((int(width*.28),int(height*.28),int(width*.98),int(height*.50)))
        signature=io.BytesIO();crop.save(signature,format='PNG',compress_level=1)
        pairs.append(Pair(number,[a[1],b[1]],output.getvalue(),[a[2],b[2]],signature.getvalue()))
    return pairs
