import csv
from collections import Counter

# Read v2 results
v2_data = []
with open('output/supplier_scraping_assessment_v2.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        v2_data.append(row)

# Stats
v2_verdicts = Counter(r['verdict'] for r in v2_data)
v2_products = {v: sum(int(r.get('product_count',0)) for r in v2_data if r.get('verdict')==v) for v in ['WORKS','MARGINAL','WONT WORK']}

# Test logs from the v2 test run
test_logs = """[1/51] DS... MARGINAL - multi_brand_no_match
[2/51] FX... [scrape] FXD-ILLSWS5P1875 NW/WS: GET https://rapidline.com.au/?s=Rapidline+Deluxe+Infinity+Single+Sided+5+P
[scrape] FXD-ILLSWS5P1875 NW/WS: ok 200 (5.0s)
[scrape] FXD-ILLSWS5P1875 NW/WS: GET https://rapidline.com.au/products/
[scrape] FXD-ILLSWS5P1875 NW/WS: ok 200 (1.4s)
[scrape] FXD-ILLSWS5P1875 NW/WS: success (name_full='Rapidline Deluxe Infinity Sing') -> https://rapidline.com.au/products/
WORKS - success
[3/51] EV... [scrape] EVBBP2: GET https://www.teaching.com.au/?s=Educational+Colours+Blackboard+Paint+2L
[scrape] EVBBP2: ok 200 (2.3s)
[scrape] EVBBP2: GET https://www.teaching.com.au/catalogue/art-and-craft/art-paint
[scrape] EVBBP2: ok 200 (0.4s)
[scrape] EVBBP2: product page verification failed (name_full)
[scrape] EVBBP2: all strategies failed (last=verification_failed)
MARGINAL - verification_failed
[4/51] SR... [scrape] SR8010-749: GET https://www.staedtler.com.au/en_AU/search/?q=Fimo+Leather-Effect+Rust
[scrape] SR8010-749: search fetch failed (name_full) - not_found
[scrape] SR8010-749: all strategies failed (last=not_found)
[scrape] SR8010-749: trying fallback domain www.staedtler.com
[scrape] SR8010-749: GET https://www.staedtler.com/intl/en/search/?q=Fimo+Leather-Effect+Rust
[scrape] SR8010-749: ok 200 (1.4s)
[scrape] SR8010-749: GET https://www.staedtler.com/intl/en/products/fimo-modelling-clay-accesso
[scrape] SR8010-749: ok 200 (1.3s)
[scrape] SR8010-749: success (name_full='Fimo Leather-Effect Rust') -> https://www.staedtler.com/intl/en/products/fimo-modelling-cl
WORKS - success
[5/51] AD... [scrape] AD936070: GET https://www.averyproducts.com.au/search?q=Avery+White+Address+and+Ship
[scrape] AD936070: http_521 (1.4s) - retrying
[scrape] AD936070: retry 1/4 in 8s
[scrape] AD936070: GET https://www.averyproducts.com.au/search?q=Avery+White+Address+and+Ship
[scrape] AD936070: ok 200 (0.8s)
[scrape] AD936070: GET https://www.averyproducts.com.au/product/clip-and-pin-name-badge-kit-9
[scrape] AD936070: http_521 (0.5s) - retrying
[scrape] AD936070: retry 1/4 in 8s
[scrape] AD936070: GET https://www.averyproducts.com.au/product/clip-and-pin-name-badge-kit-9
[scrape] AD936070: ok 200 (1.2s)
[scrape] AD936070: success (name_full='Avery White Address and Shippi') -> https://www.averyproducts.com.au/product/clip-and-pin-name-b
WORKS - success
[6/51] AH... WONT WORK - no_config
[7/51] PP... WONT WORK - no_config
[8/51] CS... [scrape] CS0119: GET https://www.teaching.com.au/?s=Candida+DL+Envelopes+Window+Face+Secret
[scrape] CS0119: ok 200 (1.0s)
[scrape] CS0119: GET https://www.teaching.com.au/catalogue/art-and-craft/art-paint/art-face
[scrape] CS0119: ok 200 (1.1s)
[scrape] CS0119: product page verification failed (name_full)
[scrape] CS0119: GET https://www.teaching.com.au/?s=Candida+DL+Envelopes+Window+Face&post_t
[scrape] CS0119: ok 200 (0.7s)
[scrape] CS0119: GET https://www.teaching.com.au/catalogue/art-and-craft/art-paint/art-face
[scrape] CS0119: ok 200 (0.3s)
[scrape] CS0119: product page verification failed (name_short)
[scrape] CS0119: all strategies failed (last=verification_failed)
MARGINAL - verification_failed
[9/51] BH... [scrape] BH10511: GET https://www.hamelinbrands.com.au/?s=3L+100852410+%2810511%29+INDEX+TAB
[scrape] BH10511: ok 200 (15.0s)
[scrape] BH10511: no product link found (name_full)
[scrape] BH10511: GET https://www.hamelinbrands.com.au/?s=3L+100852410+%2810511%29+INDEX+TAB
[scrape] BH10511: ok 200 (4.0s)
[scrape] BH10511: no product link found (name_short)
[scrape] BH10511: all strategies failed (last=no_product_link)
MARGINAL - no_product_link
[10/51] JS... WONT WORK - no_config
[11/51] GN... MARGINAL - multi_brand_no_match
[12/51] AM... MARGINAL - no_config
[13/51] VC... [scrape] VCEPBY-SKY: GET https://www.visionchart.com.au/catalogsearch/result/?q=Creative+Kids+B
[scrape] VCEPBY-SKY: search fetch failed (name_full) - not_found
[scrape] VCEPBY-SKY: GET https://www.visionchart.com.au/catalogsearch/result/?q=Creative+Kids+B
[scrape] VCEPBY-SKY: search fetch failed (name_short) - not_found
[scrape] VCEPBY-SKY: GET https://www.visionchart.com.au/catalogsearch/result/?q=EPBY-SKY
[scrape] VCEPBY-SKY: search fetch failed (sku_stripped) - not_found
[scrape] VCEPBY-SKY: GET https://www.visionchart.com.au/catalogsearch/result/?q=VCEPBY-SKY
[scrape] VCEPBY-SKY: search fetch failed (sku_raw) - not_found
[scrape] VCEPBY-SKY: all strategies failed (last=not_found)
WONT WORK - not_found
[14/51] TN... [scrape] TNNP4037: GET https://www.thenotegroup.com.au/search?q=Writer+Presentation+Flipchart
[scrape] TNNP4037: ok 200 (2.3s)
[scrape] TNNP4037: GET https://www.thenotegroup.com.au/product/compare
[scrape] TNNP4037: ok 200 (0.5s)
[scrape] TNNP4037: success (name_full='Writer Presentation Flipchart ') -> https://www.thenotegroup.com.au/product/compare
WORKS - success
[15/51] UM... WONT WORK - no_config
[16/51] WE... [scrape] WE20711: GET https://www.weatherdon.com.au/search/?q=20711
[scrape] WE20711: search fetch failed (sku_stripped) - not_found
[scrape] WE20711: GET https://www.weatherdon.com.au/search/?q=WE20711
[scrape] WE20711: search fetch failed (sku_raw) - not_found
[scrape] WE20711: GET https://www.weatherdon.com.au/search/?q=Weatherdon+Laundry+Liquid+20ml
[scrape] WE20711: search fetch failed (name_full) - not_found
[scrape] WE20711: GET https://www.weatherdon.com.au/search/?q=Weatherdon+Laundry+Liquid+20ml
[scrape] WE20711: search fetch failed (name_short) - not_found
[scrape] WE20711: all strategies failed (last=not_found)
WONT WORK - not_found
[17/51] SD... MARGINAL - no_config
[18/51] BT... WONT WORK - no_config
[19/51] FM... [scrape] FM9540802: GET https://www.fellowes.com/au/en/search.aspx?q=AeraMax+Wall+Mount+Recess
[scrape] FM9540802: search fetch failed (name_full) - not_found
[scrape] FM9540802: GET https://www.fellowes.com/au/en/search.aspx?q=AeraMax+Wall+Mount+Recess
[scrape] FM9540802: search fetch failed (name_short) - not_found
[scrape] FM9540802: all strategies failed (last=not_found)
WONT WORK - not_found
[20/51] JP... [scrape] JPMLS12: GET https://www.deflecto.com/search?q=Deflecto+12+Pocket+3+Tier+Brochure+H
[scrape] JPMLS12: search fetch failed (name_full) - not_found
[scrape] JPMLS12: GET https://www.deflecto.com/search?q=Deflecto+12+Pocket+3+Tier
[scrape] JPMLS12: search fetch failed (name_short) - not_found
[scrape] JPMLS12: GET https://www.deflecto.com/search?q=MLS12
[scrape] JPMLS12: search fetch failed (sku_stripped) - not_found
[scrape] JPMLS12: GET https://www.deflecto.com/search?q=JPMLS12
[scrape] JPMLS12: search fetch failed (sku_raw) - not_found
[scrape] JPMLS12: all strategies failed (last=not_found)
WONT WORK - not_found
[21/51] AP... [scrape] AP188961: GET https://www.hamelinbrands.com.au/?s=Mondi+Colour+Copy+Paper+120gsm+A4+
[scrape] AP188961: ok 200 (3.6s)
[scrape] AP188961: no product link found (name_full)
[scrape] AP188961: GET https://www.hamelinbrands.com.au/?s=Mondi+Colour+Copy+Paper+120gsm&pos
[scrape] AP188961: ok 200 (2.4s)
[scrape] AP188961: no product link found (name_short)
[scrape] AP188961: all strategies failed (last=no_product_link)
MARGINAL - no_product_link
[22/51] 3M... [scrape] 3M98044054124: GET https://www.post-it.com/3M/en_US/post-it/search/?q=3M+Privacy+Filter+f
[scrape] 3M98044054124: search fetch failed (name_full) - not_found
[scrape] 3M98044054124: GET https://www.post-it.com/3M/en_US/post-it/search/?q=3M+Privacy+Filter+f
[scrape] 3M98044054124: search fetch failed (name_short) - not_found
[scrape] 3M98044054124: all strategies failed (last=not_found)
WONT WORK - not_found
[23/51] CO... [scrape] COKW-952-PACK: GET https://www.colby.com.au/?s=KW+BY+COLBY+2xCUTTER%2F4xDISC+PACK+KW-952&
[scrape] COKW-952-PACK: ok 200 (4.7s)
[scrape] COKW-952-PACK: no product link found (name_full)
[scrape] COKW-952-PACK: GET https://www.colby.com.au/?s=KW+BY+COLBY+2xCUTTER%2F4xDISC+PACK&post_ty
[scrape] COKW-952-PACK: ok 200 (1.1s)
[scrape] COKW-952-PACK: no product link found (name_short)
[scrape] COKW-952-PACK: all strategies failed (last=no_product_link)
MARGINAL - no_product_link
[24/51] PH... [scrape] PHMQUPANB989: GET https://www.phe.com.au/?s=PHE+Qupa+NB989+Electric+Binding+Punch+System
[scrape] PHMQUPANB989: ok 200 (3.1s)
[scrape] PHMQUPANB989: GET https://www.phe.com.au/shop/print-finishing/staples/nagel-machine-stap
[scrape] PHMQUPANB989: ok 200 (3.0s)
[scrape] PHMQUPANB989: success (name_full='PHE Qupa NB989 Electric Bindin') -> https://www.phe.com.au/shop/print-finishing/staples/nagel-ma
WORKS - success
[25/51] DO... [scrape] DODKTL0010: GET https://dolphy.com.au/?s=Dolphy+0.8L+Stainless+Steel+Electric+Kettle+S
[scrape] DODKTL0010: ok 200 (1.5s)
[scrape] DODKTL0010: GET https://dolphy.com.au/pages/catalogue-page
[scrape] DODKTL0010: ok 200 (0.3s)
[scrape] DODKTL0010: success (name_full='Dolphy 0.8L Stainless Steel El') -> https://dolphy.com.au/pages/catalogue-page
WORKS - success
[26/51] IT... WONT WORK - no_config
[27/51] KC... [scrape] KC4735: GET https://www.kcprofessional.com/en-au/search?q=Kleenex+Toilet+Tissue+Wh
[scrape] KC4735: ok 200 (3.1s)
[scrape] KC4735: no results page (name_full='Kleenex Toilet Tissue White 2 ')
[scrape] KC4735: GET https://www.kcprofessional.com/en-au/search?q=Kleenex+Toilet+Tissue+Wh
[scrape] KC4735: ok 200 (1.6s)
[scrape] KC4735: no results page (name_short='Kleenex Toilet Tissue White 2')
[scrape] KC4735: all strategies failed (last=not_found)
WONT WORK - not_found
[28/51] ZN... [scrape] ZN212: GET https://www.thenotegroup.com.au/search?q=Zions+Business+Income+and+Exp
[scrape] ZN212: connection_error - ('Connection aborted.', ConnectionResetError(10054, 'An exis
[scrape] ZN212: retry 1/2 in 5s
[scrape] ZN212: GET https://www.thenotegroup.com.au/search?q=Zions+Business+Income+and+Exp
[scrape] ZN212: ok 200 (1.5s)
[scrape] ZN212: GET https://www.thenotegroup.com.au/product/compare
[scrape] ZN212: ok 200 (0.5s)
[scrape] ZN212: success (name_full='Zions Business Income and Expe') -> https://www.thenotegroup.com.au/product/compare
WORKS - success
[29/51] ER... [scrape] ER5BBP: GET https://www.teaching.com.au/?s=Elizabeth+Richards+Book+Box+Pack+of+5+P
[scrape] ER5BBP: ok 200 (2.3s)
[scrape] ER5BBP: GET https://www.teaching.com.au/catalogue/childrens-books/childrens-big-bo
[scrape] ER5BBP: ok 200 (0.2s)
[scrape] ER5BBP: product page verification failed (name_full)
[scrape] ER5BBP: GET https://www.teaching.com.au/?s=Elizabeth+Richards+Book+Box+Pack&post_t
[scrape] ER5BBP: ok 200 (0.7s)
[scrape] ER5BBP: GET https://www.teaching.com.au/catalogue/childrens-books/childrens-big-bo
[scrape] ER5BBP: ok 200 (0.3s)
[scrape] ER5BBP: product page verification failed (name_short)
[scrape] ER5BBP: all strategies failed (last=verification_failed)
MARGINAL - verification_failed
[30/51] AR... [scrape] ARB022: GET https://arnos.com.au/?s=Arnos+B022+Swingsign+5+Panel+Expansion+and+Wal
[scrape] ARB022: ok 200 (2.8s)
[scrape] ARB022: no product link found (name_full)
[scrape] ARB022: GET https://arnos.com.au/?s=Arnos+B022+Swingsign+5+Panel&post_type=product
[scrape] ARB022: ok 200 (1.4s)
[scrape] ARB022: no product link found (name_short)
[scrape] ARB022: all strategies failed (last=no_product_link)
MARGINAL - no_product_link
[31/51] CD... [scrape] CD10232: GET https://www.collinsdebden.com.au/search?q=Collins+10232+Account+Book+S
[scrape] CD10232: ok 200 (2.4s)
[scrape] CD10232: GET https://www.collinsdebden.com.au/products/account-book-series-a24-minu
[scrape] CD10232: ok 200 (0.6s)
[scrape] CD10232: success (name_full='Collins 10232 Account Book Ser') -> https://www.collinsdebden.com.au/products/account-book-serie
WORKS - success
[32/51] BA... WONT WORK - no_config
[33/51] SL... [scrape] SLQL-1100: GET https://www.brother.com.au/en/search?q=Brother+QL1100+Label+Printer
[scrape] SLQL-1100: ok 200 (3.6s)
[scrape] SLQL-1100: GET https://www.brother.com.au/en/product-registration-benefits
[scrape] SLQL-1100: ok 200 (1.2s)
[scrape] SLQL-1100: product page verification failed (name_full)
[scrape] SLQL-1100: all strategies failed (last=verification_failed)
MARGINAL - verification_failed
[34/51] BR... WONT WORK - no_config
[35/51] GS... [scrape] GSBCAYAR1: GET https://spencil.com.au/?s=Spencil+Yarrawala+1+A4+Book+Cover+Pack+of+6&
[scrape] GSBCAYAR1: ok 200 (2.3s)
[scrape] GSBCAYAR1: no product link found (name_full)
[scrape] GSBCAYAR1: GET https://spencil.com.au/?s=Spencil+Yarrawala+1+A4+Book&post_type=produc
[scrape] GSBCAYAR1: ok 200 (0.3s)
[scrape] GSBCAYAR1: no product link found (name_short)
[scrape] GSBCAYAR1: all strategies failed (last=no_product_link)
MARGINAL - no_product_link
[36/51] BX... [scrape] BX2034-38: GET https://www.hamelinbrands.com.au/?s=Bantex+A4+Sheet+Protectors+Economy
[scrape] BX2034-38: ok 200 (5.4s)
[scrape] BX2034-38: no product link found (name_full)
[scrape] BX2034-38: GET https://www.hamelinbrands.com.au/?s=Bantex+A4+Sheet+Protectors+Economy
[scrape] BX2034-38: ok 200 (3.8s)
[scrape] BX2034-38: no product link found (name_short)
[scrape] BX2034-38: all strategies failed (last=no_product_link)
MARGINAL - no_product_link
[37/51] JA... [scrape] JA0384304: GET https://www.jasco.com.au/search?q=0384304
[scrape] JA0384304: ok 200 (5.3s)
[scrape] JA0384304: GET https://www.jasco.com.au/products/whats-new
[scrape] JA0384304: ok 200 (0.8s)
[scrape] JA0384304: success (sku_stripped='0384304') -> https://www.jasco.com.au/products/whats-new
WORKS - success
[38/51] FC... [scrape] FC18-110072: GET https://www.faber-castell.com.au/products/search?q=Faber+Castell+Polyc
[scrape] FC18-110072: ok 200 (7.7s)
[scrape] FC18-110072: no results page (name_full='Faber Castell Polychromos Colo')
[scrape] FC18-110072: GET https://www.faber-castell.com.au/products/search?q=Faber+Castell+Polyc
[scrape] FC18-110072: ok 200 (2.5s)
[scrape] FC18-110072: GET https://www.faber-castell.com.au/products/24-25-03-colored-pencil
[scrape] FC18-110072: ok 200 (0.8s)
[scrape] FC18-110072: success (name_short='Faber Castell Polychromos Colo') -> https://www.faber-castell.com.au/products/24-25-03-colored-p
WORKS - success
[39/51] AB... WONT WORK - no_config
[40/51] LA... MARGINAL - no_config
[41/51] RH... [scrape] RHWS171AS4: GET https://www.teaching.com.au/?s=Nyda+Airflow+Softball+Set+of+4&post_typ
[scrape] RHWS171AS4: connection_error - ('Connection aborted.', ConnectionResetError(10054, 'An exis
[scrape] RHWS171AS4: retry 1/2 in 5s
[scrape] RHWS171AS4: GET https://www.teaching.com.au/?s=Nyda+Airflow+Softball+Set+of+4&post_typ
[scrape] RHWS171AS4: ok 200 (2.2s)
[scrape] RHWS171AS4: GET https://www.teaching.com.au/catalogue/art-and-craft/art-paint-brushes/
[scrape] RHWS171AS4: ok 200 (0.3s)
[scrape] RHWS171AS4: product page verification failed (name_full)
[scrape] RHWS171AS4: GET https://www.teaching.com.au/?s=Nyda+Airflow+Softball+Set+of&post_type=
[scrape] RHWS171AS4: ok 200 (0.7s)
[scrape] RHWS171AS4: GET https://www.teaching.com.au/catalogue/art-and-craft/art-paint-brushes/
[scrape] RHWS171AS4: ok 200 (0.3s)
[scrape] RHWS171AS4: product page verification failed (name_short)
[scrape] RHWS171AS4: all strategies failed (last=verification_failed)
MARGINAL - verification_failed
[42/51] DU... WONT WORK - no_config
[43/51] CC... MARGINAL - no_config
[44/51] PE... [scrape] PEN50-D: GET https://www.pentel.com.au/search?q=Pentel+N50+Permanent+Marker+Bullet+
[scrape] PEN50-D: connection_error - HTTPSConnectionPool(host='www.pentel.com.au', port=443): Max
[scrape] PEN50-D: retry 1/2 in 5s
[scrape] PEN50-D: GET https://www.pentel.com.au/search?q=Pentel+N50+Permanent+Marker+Bullet+
[scrape] PEN50-D: connection_error - HTTPSConnectionPool(host='www.pentel.com.au', port=443): Max
[scrape] PEN50-D: retry 2/2 in 10s
[scrape] PEN50-D: GET https://www.pentel.com.au/search?q=Pentel+N50+Permanent+Marker+Bullet+
[scrape] PEN50-D: ok 200 (3.5s)
[scrape] PEN50-D: no product link found (name_full)
[scrape] PEN50-D: GET https://www.pentel.com.au/search?q=Pentel+N50+Permanent+Marker+Bullet
[scrape] PEN50-D: ok 200 (2.2s)
[scrape] PEN50-D: no product link found (name_short)
[scrape] PEN50-D: all strategies failed (last=no_product_link)
MARGINAL - no_product_link
[45/51] ST... WONT WORK - no_config
[46/51] GG... [scrape] GGWGACDL48: GET https://au.whogivesacrap.org/search?q=Who+Gives+a+Crap+Recycled+Toilet
[scrape] GGWGACDL48: ok 200 (2.0s)
[scrape] GGWGACDL48: GET https://au.whogivesacrap.org/products/the-splash-limited-edition
[scrape] GGWGACDL48: ok 200 (0.3s)
[scrape] GGWGACDL48: success (name_full='Who Gives a Crap Recycled Toil') -> https://au.whogivesacrap.org/products/the-splash-limited-edi
WORKS - success
[47/51] ZA... [scrape] ZAWP064: GET https://www.teaching.com.au/?s=Creative+School+Supply+Surfboard+with+S
[scrape] ZAWP064: ok 200 (0.7s)
[scrape] ZAWP064: GET https://www.teaching.com.au/catalogue/art-and-craft/art-art-room-equip
[scrape] ZAWP064: ok 200 (0.3s)
[scrape] ZAWP064: product page verification failed (name_full)
[scrape] ZAWP064: GET https://www.teaching.com.au/?s=Creative+School+Supply+Surfboard+with&p
[scrape] ZAWP064: ok 200 (0.5s)
[scrape] ZAWP064: GET https://www.teaching.com.au/catalogue/art-and-craft/art-art-room-equip
[scrape] ZAWP064: ok 200 (0.5s)
[scrape] ZAWP064: product page verification failed (name_short)
[scrape] ZAWP064: all strategies failed (last=verification_failed)
MARGINAL - verification_failed
[48/51] BF... WONT WORK - no_config
[49/51] KP... [scrape] KP7230: GET https://www.jshayes.com.au/?s=Hamilton+Active+Family+SPF50%2B+Sunscree
[scrape] KP7230: ok 200 (3.0s)
[scrape] KP7230: GET https://www.jshayes.com.au/products/paper-products
[scrape] KP7230: ok 200 (1.9s)
[scrape] KP7230: extracted empty content (name_full)
[scrape] KP7230: GET https://www.jshayes.com.au/?s=Hamilton+Active+Family+SPF50%2B+Sunscree
[scrape] KP7230: ok 200 (0.6s)
[scrape] KP7230: GET https://www.jshayes.com.au/products/paper-products
[scrape] KP7230: ok 200 (4.3s)
[scrape] KP7230: extracted empty content (name_short)
[scrape] KP7230: all strategies failed (last=scraped_empty)
MARGINAL - scraped_empty
[50/51] EC... [scrape] ECCS2JB2L: GET https://www.teaching.com.au/?s=EC+CLASSROOM+SPLASH+2LT+JELLY+BELLY+BLU
[scrape] ECCS2JB2L: ok 200 (0.5s)
[scrape] ECCS2JB2L: GET https://www.teaching.com.au/catalogue/art-and-craft/art-art-room-acces
[scrape] ECCS2JB2L: ok 200 (0.7s)
[scrape] ECCS2JB2L: product page verification failed (name_full)
[scrape] ECCS2JB2L: GET https://www.teaching.com.au/?s=EC+CLASSROOM+SPLASH+2LT+JELLY&post_type
[scrape] ECCS2JB2L: ok 200 (5.7s)
[scrape] ECCS2JB2L: GET https://www.teaching.com.au/catalogue/art-and-craft/art-art-room-acces
[scrape] ECCS2JB2L: ok 200 (9.0s)
[scrape] ECCS2JB2L: product page verification failed (name_short)
[scrape] ECCS2JB2L: all strategies failed (last=verification_failed)
MARGINAL - verification_failed
[51/51] JH... [scrape] JH0477829: GET https://www.jshayes.com.au/?s=Tork+Black+Cocktail+Napkin+Pack+of+200+C
[scrape] JH0477829: ok 200 (2.1s)
[scrape] JH0477829: GET https://www.jshayes.com.au/products/paper-products
[scrape] JH0477829: ok 200 (0.8s)
[scrape] JH0477829: extracted empty content (name_full)
[scrape] JH0477829: GET https://www.jshayes.com.au/?s=Tork+Black+Cocktail+Napkin+Pack&post_typ
[scrape] JH0477829: ok 200 (1.6s)
[scrape] JH0477829: GET https://www.jshayes.com.au/products/paper-products
[scrape] JH0477829: ok 200 (0.8s)
[scrape] JH0477829: extracted empty content (name_short)
[scrape] JH0477829: all strategies failed (last=scraped_empty)
MARGINAL - scraped_empty"""

# V2 features
v2_features = """## Scraper v2 Key Improvements

### 1. Multi-Strategy Search
- **v1**: Only used Product Name search
- **v2**: Tries MPN first, then Product Name, then SKU variants
- **Impact**: More accurate product matching

### 2. Product Page Verification
- **v1**: Accepted any returned page from search
- **v2**: Verifies scraped page matches target product using fuzzy matching
- **Impact**: Rejects incorrect pages, more accurate data

### 3. Link Scoring
- **v1**: Took first search result
- **v2**: Scores all candidate links and picks highest score
- **Impact**: Better selection when multiple similar products exist

### 4. Retry Logic
- **v1**: No retry mechanism
- **v2**: Automatic retry on HTTP 521, 503, 429 with exponential backoff (up to 4 retries)
- **Impact**: Handles transient server errors

### 5. Extract Fallback
- **v1**: Only used custom CSS selectors
- **v2**: Falls back to generic main-content extraction if custom selectors fail
- **Impact**: More resilient to site changes

### 6. Fallback Domains
- **v1**: Only tried primary domain
- **v2**: Tries alternative domains (e.g., .com.au -> .com international)
- **Impact**: Staedtler now works via staedtler.com

### 7. Configuration Options
```python
search_strategies: ["mpn", "name"],  # Try MPN first
score_links: True,                   # Score all candidates
verify_product: True,                # Verify page matches
extract_fallback: True,              # Use generic fallback
retry_on: [521, 503, 429],           # Retry on these codes
max_retries: 4,                      # Max 4 retries
```"""

# Build report
report = f"""# Supplier Scraping Assessment Report - SCRAPER v2
Generated: 2026-06-18

## Executive Summary

This report assesses the scraping capability for all 51 suppliers using Scraper v2.

### Results Overview

| Verdict | Suppliers | Products |
|---------|-----------|----------|
| WORKS | {v2_verdicts.get('WORKS',0)} | {v2_products.get('WORKS',0):,} |
| MARGINAL | {v2_verdicts.get('MARGINAL',0)} | {v2_products.get('MARGINAL',0):,} |
| WONT WORK | {v2_verdicts.get('WONT WORK',0)} | {v2_products.get('WONT WORK',0):,} |

### Verdict Definitions
- **WORKS**: Search finds products, content successfully extracted
- **MARGINAL**: Partial functionality - needs improvements or has limiting factors
- **WONT WORK**: Cannot scrape - no config, blocked, or not found

---

{v2_features}

---

## Test Logs

Full test run output from 2026-06-18:

```
{test_logs}
```

---

## Supplier-by-Supplier Results

"""

for r in v2_data:
    report += f"""### {r['prefix']} - {r['supplier']}
- **Domain**: {r['domain']}
- **Products**: {int(r['product_count']):,}
- **Status**: {r['status']}
- **Verdict**: {r['verdict']}
- **Reason**: {r['reason']}
- **Test SKU**: {r['test_sku']}
- **Test Title**: {r['test_title']}

"""

# Save
with open('output/supplier_scraping_assessment_v2_REPORT.md', 'w', encoding='utf-8') as f:
    f.write(report)

print('Saved: output/supplier_scraping_assessment_v2_REPORT.md')
print(f'WORKS: {v2_verdicts.get("WORKS",0)} suppliers ({v2_products.get("WORKS",0):,} products)')
print(f'MARGINAL: {v2_verdicts.get("MARGINAL",0)} suppliers ({v2_products.get("MARGINAL",0):,} products)')
print(f'WONT WORK: {v2_verdicts.get("WONT WORK",0)} suppliers ({v2_products.get("WONT WORK",0):,} products)')