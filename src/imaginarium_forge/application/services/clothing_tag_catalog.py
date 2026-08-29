"""Supplemental flat clothing tags for the character prompt builder.

The tuples in this module deliberately contain only data.  Keeping them
independent from ``prompt_tag_builder_service`` avoids a circular import while
allowing that service to construct its own ``TagOption`` instances.
"""

from typing import Final

type ClothingTagDefinition = tuple[str, str, str]


OUTFIT_ARCHETYPE_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("t_shirt", "T恤", "t-shirt"),
    ("shirt", "襯衫", "shirt"),
    ("blouse", "女式襯衫", "blouse"),
    ("tank_top", "背心", "tank top"),
    ("camisole", "細肩帶背心", "camisole"),
    ("tube_top", "平口上衣", "tube top"),
    ("halter_top", "掛脖上衣", "halter top"),
    ("crop_top", "短版上衣", "crop top"),
    ("polo_shirt", "Polo 衫", "polo shirt"),
    ("jersey", "球衣", "jersey"),
    ("sweater", "毛衣", "sweater"),
    ("hoodie", "帽T", "hoodie"),
    ("sweatshirt", "大學T", "sweatshirt"),
    ("cardigan", "開襟衫", "cardigan"),
    ("vest", "西裝背心／馬甲", "waistcoat"),
    ("turtleneck_top", "高領上衣", "turtleneck top"),
    ("trousers", "長褲", "trousers"),
    ("jeans", "牛仔褲", "jeans"),
    ("shorts", "短褲", "shorts"),
    ("hot_pants", "超短褲", "hot pants"),
    ("cargo_pants", "工裝褲", "cargo pants"),
    ("joggers", "束口褲", "joggers"),
    ("sweatpants", "休閒運動褲", "sweatpants"),
    ("leggings", "緊身褲", "leggings"),
    ("yoga_pants", "瑜珈褲", "yoga pants"),
    ("cycling_shorts", "單車褲", "cycling shorts"),
    ("culottes", "寬褲／裙褲", "culottes"),
    ("skirt", "裙子", "skirt"),
    ("pleated_skirt", "百褶裙", "pleated skirt"),
    ("a_line_skirt", "A字裙", "a-line skirt"),
    ("pencil_skirt", "鉛筆裙", "pencil skirt"),
    ("circle_skirt", "圓裙", "circle skirt"),
    ("wrap_skirt", "裹裙", "wrap skirt"),
    ("denim_skirt", "牛仔裙", "denim skirt"),
    ("tennis_skirt", "網球裙", "tennis skirt"),
    ("dress", "洋裝", "dress"),
    ("sundress", "夏季洋裝", "sundress"),
    ("evening_gown", "晚禮服", "evening gown"),
    ("slip_dress", "細肩帶洋裝", "slip dress"),
    ("bodycon_dress", "貼身洋裝", "bodycon dress"),
    ("shirt_dress", "襯衫洋裝", "shirt dress"),
    ("sweater_dress", "毛衣洋裝", "sweater dress"),
    ("jumpsuit", "連身長褲", "jumpsuit"),
    ("romper", "連身短褲", "romper"),
    ("overalls", "吊帶褲", "overalls"),
    ("bodysuit", "連體衣", "bodysuit"),
    ("jacket", "夾克", "jacket"),
    ("coat", "大衣", "coat"),
    ("blazer", "西裝外套", "blazer"),
    ("parka", "派克大衣", "parka"),
    ("puffer_jacket", "羽絨外套", "puffer jacket"),
    ("denim_jacket", "牛仔外套", "denim jacket"),
    ("leather_jacket", "皮衣", "leather jacket"),
    ("bomber_jacket", "飛行外套", "bomber jacket"),
    ("windbreaker", "防風外套", "windbreaker jacket"),
    ("cloak", "斗篷", "cloak"),
    ("raincoat", "雨衣", "raincoat"),
    ("bra", "胸罩", "bra"),
    ("bralette", "Bralette 無鋼圈內衣", "bralette"),
    ("sports_bra", "運動內衣", "sports bra"),
    ("strapless_bra", "無肩帶內衣", "strapless bra"),
    ("bandeau_bra", "平口內衣", "bandeau bra"),
    ("push_up_bra", "集中型內衣", "push-up bra"),
    ("balconette_bra", "半罩杯內衣", "balconette bra"),
    ("triangle_bra", "三角罩杯", "triangle bra"),
    ("panties", "女式內褲", "panties"),
    ("briefs", "三角內褲", "briefs"),
    ("bikini_briefs", "比基尼型內褲", "bikini briefs"),
    ("hipster_briefs", "低腰平口內褲", "hipster panties"),
    ("boyshorts", "女式平口褲", "boyshorts"),
    ("high_waisted_briefs", "高腰內褲", "high-waisted panties"),
    ("seamless_underwear", "無痕內褲", "seamless panties"),
    ("thong", "丁字褲", "thong"),
    ("g_string", "G-string 細帶內褲", "g-string"),
    ("tanga", "Tanga 內褲", "tanga panties"),
    ("cheeky_panties", "半包臀內褲", "cheeky panties"),
    ("lace_panties", "蕾絲內褲", "lace panties"),
    ("side_tie_panties", "側綁帶內褲", "side-tie panties"),
    ("high_cut_panties", "高衩內褲", "high-cut panties"),
    ("pajamas", "睡衣", "pajamas"),
    ("pajama_top", "睡衣上衣", "pajama top"),
    ("pajama_pants", "睡褲", "pajama pants"),
    ("pajama_shorts", "睡衣短褲", "pajama shorts"),
    ("nightgown", "睡裙", "nightgown"),
    ("nightshirt", "睡衣襯衫", "nightshirt"),
    ("robe", "睡袍", "robe"),
    ("bathrobe", "浴袍", "bathrobe"),
    ("loungewear", "居家服", "loungewear"),
    ("onesie", "連身睡衣", "onesie"),
    ("dress_shirt", "正裝襯衫", "dress shirt"),
    ("dress_pants", "西裝褲", "dress pants"),
    ("chinos", "卡其褲", "chinos"),
    ("suit_jacket", "西裝上衣", "suit jacket"),
    ("undershirt", "汗衫", "undershirt"),
    ("boxers", "四角寬鬆內褲", "boxers"),
    ("boxer_briefs", "貼身四角褲", "boxer briefs"),
    ("trunks", "短版四角褲", "trunks"),
    ("jockstrap", "運動護襠內褲", "jockstrap"),
    ("sports_shirt", "運動上衣", "sports shirt"),
    ("sports_shorts", "運動短褲", "sports shorts"),
    ("compression_shirt", "壓縮衣", "compression shirt"),
    ("compression_tights", "壓縮褲", "compression tights"),
    ("track_pants", "運動套裝長褲", "track pants"),
    ("tracksuit", "運動套裝", "tracksuit"),
    ("swim_briefs", "三角泳褲", "swim briefs"),
    ("swim_trunks", "泳褲", "swim trunks"),
    ("board_shorts", "衝浪褲", "board shorts"),
    ("jammer", "五分競泳褲", "swim jammer"),
    ("rash_guard", "防磨／防曬泳衣", "rash guard"),
    ("bikini", "比基尼", "bikini"),
    ("one_piece_swimsuit", "一件式泳裝", "one-piece swimsuit"),
    ("tankini", "Tankini 兩件式泳裝", "tankini"),
    ("monokini", "Monokini 挖空連身泳裝", "monokini"),
    ("swim_dress", "泳裙", "swim dress"),
    ("competitive_swimsuit", "競泳泳裝", "competitive swimsuit"),
    ("school_swimsuit", "學校泳裝（死庫水）", "school swimsuit"),
    ("cutout_swimsuit", "挖空泳裝", "cutout swimsuit"),
    ("high_leg_swimsuit", "高衩泳裝", "high-leg swimsuit"),
    ("frilled_bikini", "荷葉邊比基尼", "frilled bikini"),
    ("ruched_bikini", "抓皺比基尼", "ruched bikini"),
    ("triangle_bikini", "三角比基尼", "triangle bikini"),
    ("halter_bikini", "掛脖比基尼", "halter bikini"),
    ("bandeau_bikini", "平口比基尼", "bandeau bikini"),
    ("string_bikini", "綁帶比基尼", "string bikini"),
    ("high_waisted_bikini", "高腰比基尼", "high-waisted bikini"),
    ("sports_bikini", "運動比基尼", "sports bikini"),
    ("underwire_bikini", "鋼圈比基尼", "underwire bikini"),
    ("side_tie_bikini", "側綁帶比基尼", "side-tie bikini"),
    ("skirted_bikini", "裙式比基尼", "skirted bikini"),
    ("high_cut_one_piece", "高衩一件式泳裝", "high-cut one-piece swimsuit"),
    ("backless_swimsuit", "露背泳裝", "backless swimsuit"),
    ("zip_front_swimsuit", "前拉鍊泳裝", "zip-front swimsuit"),
    ("racing_one_piece", "競速型一件式泳裝", "racing one-piece swimsuit"),
    ("utility_jumpsuit", "工裝連身服", "utility jumpsuit"),
    ("poncho", "斗篷式披肩", "poncho"),
    ("wrap_dress", "裹身洋裝", "wrap dress"),
)


OUTFIT_ARCHETYPE_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    ("micro_bikini", "極小比基尼（18+）", "micro bikini"),
    ("slingshot_swimsuit", "吊帶式泳裝（18+）", "slingshot swimsuit"),
    ("lingerie_set", "情趣內衣套裝（18+）", "lingerie set"),
    ("babydoll", "Babydoll 睡衣（18+）", "babydoll lingerie"),
    ("lingerie_chemise", "Chemise 情趣睡衣（18+）", "lingerie chemise"),
    ("teddy_lingerie", "Teddy 連身內衣（18+）", "teddy lingerie"),
    ("corset", "馬甲（18+）", "corset"),
    ("bustier", "Bustier 馬甲上衣（18+）", "bustier"),
    ("basque", "Basque 馬甲式內衣（18+）", "basque lingerie"),
    ("bodystocking", "連身襪衣／全身貼身襪衣（18+）", "bodystocking"),
    ("sheer_bodysuit", "透膚連身衣（18+）", "sheer bodysuit"),
    ("lace_bodysuit", "蕾絲連身衣（18+）", "lace bodysuit"),
    ("mesh_bodysuit", "網紗連身衣（18+）", "mesh bodysuit"),
    ("garter_lingerie_set", "吊襪帶套組（18+）", "garter lingerie set"),
    ("cupless_bra", "開杯內衣（18+）", "cupless bra"),
    ("front_closure_bra", "前開式內衣（18+）", "front-closure bra"),
    ("high_cut_bodysuit", "高衩連身衣（18+）", "high-cut bodysuit"),
    ("strappy_panties", "綁帶內褲（18+）", "strappy panties"),
    ("lace_thong", "蕾絲丁字褲（18+）", "lace thong"),
    ("mesh_panties", "網紗內褲（18+）", "mesh panties"),
    ("sheer_panties", "透膚內褲（18+）", "sheer panties"),
    ("crotchless_panties", "開襠內褲（18+）", "crotchless panties"),
    ("open_back_panties", "露臀內褲（18+）", "open-back panties"),
    ("cutout_panties", "挖空內褲（18+）", "cutout panties"),
    ("side_tie_thong", "側綁帶丁字褲（18+）", "side-tie thong"),
    ("sheer_robe", "透紗睡袍（18+）", "sheer robe"),
    ("lace_robe", "蕾絲睡袍（18+）", "lace robe"),
    ("silk_robe", "絲質睡袍（18+）", "silk robe"),
    ("satin_nightgown", "緞面睡裙（18+）", "satin nightgown"),
    ("lace_nightgown", "蕾絲睡裙（18+）", "lace nightgown"),
    ("slip_nightdress", "細肩帶睡裙（18+）", "slip nightdress"),
    ("see_through_nightwear", "透視睡衣（18+）", "see-through nightwear"),
    ("extreme_micro_bikini", "極限微型比基尼（18+）", "extreme micro bikini"),
    (
        "string_one_piece_swimsuit",
        "細帶泳裝（18+）",
        "string one-piece swimsuit",
    ),
    ("adult_lace_cage_dress", "蕾絲籠狀情趣裙（18+）", "lace cage lingerie dress"),
    (
        "adult_strappy_cage_bodysuit",
        "多帶籠狀連身衣（18+）",
        "strappy cage bodysuit",
    ),
    (
        "adult_underbust_corset_set",
        "下胸馬甲內衣套組（18+）",
        "underbust corset lingerie set",
    ),
    ("adult_open_cup_teddy", "開杯連身內衣（18+）", "open-cup teddy lingerie"),
    (
        "adult_fishnet_mini_dress",
        "漁網迷你情趣裙（18+）",
        "fishnet mini lingerie dress",
    ),
    (
        "adult_suspender_bodysuit",
        "吊襪帶連身內衣（18+）",
        "lingerie bodysuit with attached garters",
    ),
    (
        "adult_pearl_string_lingerie",
        "珍珠細帶內衣（18+）",
        "pearl-strand lingerie",
    ),
    ("adult_keyhole_bodysuit", "鑰匙孔挖空連身衣（18+）", "keyhole bodysuit"),
    ("adult_backless_teddy", "露背連身內衣（18+）", "backless teddy lingerie"),
    ("adult_open_side_bodysuit", "側開連身衣（18+）", "open-side bodysuit"),
    (
        "adult_strappy_garter_dress",
        "綁帶吊襪情趣裙（18+）",
        "strappy garter lingerie dress",
    ),
    ("adult_chainmail_bikini", "鍊甲比基尼（18+）", "chainmail bikini"),
    (
        "adult_sheer_halter_teddy",
        "透膚掛脖連身內衣（18+）",
        "sheer halter teddy lingerie",
    ),
    (
        "adult_open_back_lingerie_set",
        "露背情趣內衣套組（18+）",
        "open-back lingerie set",
    ),
)


ACCESSORIES_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("baseball_cap", "棒球帽", "baseball cap"),
    ("hat", "帽子", "hat"),
    ("beret", "貝雷帽", "beret"),
    ("beanie", "毛帽", "beanie"),
    ("sun_hat", "遮陽帽", "sun hat"),
    ("hair_ribbon", "髮飾緞帶", "hair ribbon"),
    ("hair_clip", "髮夾", "hair clip"),
    ("hair_claw", "抓夾", "hair claw clip"),
    ("scrunchie", "髮圈", "scrunchie"),
    ("headband", "髮箍", "headband"),
    ("bandana", "方巾／頭巾", "bandana"),
    ("tie", "領帶", "necktie"),
    ("bow_tie", "蝴蝶領結", "bow tie"),
    ("bracelet", "手鍊", "bracelet"),
    ("anklet", "腳鍊", "anklet"),
    ("fingerless_gloves", "無指手套", "fingerless gloves"),
    ("mittens", "連指手套", "mittens"),
    ("wristband", "運動護腕", "athletic wristband"),
    ("watch", "手錶", "wristwatch"),
    ("belt", "腰帶", "belt"),
    ("waist_chain", "腰鍊", "waist chain"),
    ("suspenders", "吊褲帶", "suspenders"),
    ("backpack", "後背包", "backpack"),
    ("handbag", "手提包", "handbag"),
    ("shoulder_bag", "肩背包", "shoulder bag"),
    ("crossbody_bag", "斜背包", "crossbody bag"),
    ("clutch", "手拿包", "clutch bag"),
    ("coin_purse", "零錢包", "coin purse"),
    ("socks", "一般襪子", "socks"),
    ("stockings", "長絲襪／長襪", "stockings"),
    ("garter_stockings", "吊帶絲襪", "garter stockings"),
    ("pantyhose", "薄型褲襪", "pantyhose"),
    ("tights", "厚型褲襪", "tights"),
    ("leg_warmers", "腿套", "leg warmers"),
    ("bucket_hat", "漁夫帽", "bucket hat"),
    ("newsboy_cap", "報童帽", "newsboy cap"),
    ("fascinator", "小禮帽頭飾", "fascinator headpiece"),
    ("lapel_pin", "領針", "decorative lapel pin"),
    ("cufflinks", "袖扣", "ornate cufflinks"),
    ("pocket_square", "口袋巾", "folded pocket square"),
)


ACCESSORIES_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    ("adult_accessory_garter_belt", "吊襪帶（18+）", "garter belt"),
    ("adult_accessory_sheer_coverup", "透紗罩衫（18+）", "sheer cover-up"),
    ("adult_accessory_sarong", "沙龍裙／罩裙（18+）", "sarong"),
    (
        "adult_accessory_lace_top_stockings",
        "蕾絲邊絲襪（18+）",
        "lace-top stockings",
    ),
    (
        "adult_accessory_fishnet_stockings",
        "漁網絲襪（18+）",
        "fishnet stockings",
    ),
    (
        "adult_accessory_fishnet_pantyhose",
        "漁網褲襪（18+）",
        "fishnet pantyhose",
    ),
    ("adult_accessory_seamed_stockings", "後線絲襪（18+）", "seamed stockings"),
    (
        "adult_accessory_suspender_tights",
        "吊帶造型褲襪（18+）",
        "suspender tights",
    ),
    ("adult_accessory_thigh_garter", "大腿環／腿環（18+）", "thigh garter"),
    ("adult_accessory_leg_harness", "腿部束帶（18+）", "leg harness"),
    ("adult_accessory_collar", "項圈（18+）", "decorative neck collar"),
    ("adult_accessory_leather_collar", "皮質項圈（18+）", "leather collar"),
    ("adult_accessory_lace_choker", "蕾絲頸圈（18+）", "lace choker"),
    ("adult_accessory_nipple_pasties", "胸貼（18+）", "nipple pasties"),
    ("adult_accessory_arm_cuffs", "手臂環（18+）", "arm cuffs"),
    ("adult_accessory_lace_mask", "蕾絲面罩（18+）", "lace masquerade mask"),
    ("adult_accessory_opera_gloves", "長手套（18+）", "opera gloves"),
    (
        "adult_accessory_detachable_collar",
        "可拆式項圈（18+）",
        "detachable intimate collar",
    ),
    (
        "adult_accessory_thigh_cuffs",
        "情趣大腿環銬（18+）",
        "decorative intimate thigh cuffs",
    ),
    (
        "adult_accessory_lace_wristlets",
        "蕾絲腕飾（18+）",
        "delicate lace wristlets",
    ),
    (
        "adult_accessory_satin_blindfold",
        "緞面眼罩（18+）",
        "decorative satin blindfold",
    ),
    (
        "adult_accessory_hip_chain",
        "臀胯鍊飾（18+）",
        "draped hip-chain jewelry",
    ),
    (
        "adult_accessory_body_garters",
        "身體吊帶環（18+）",
        "decorative body garter harness",
    ),
    (
        "adult_accessory_pearl_body_strands",
        "珍珠身體鍊（18+）",
        "draped pearl body chain",
    ),
    (
        "adult_accessory_feather_eye_mask",
        "羽飾情趣眼罩（18+）",
        "decorative feathered eye mask",
    ),
    (
        "adult_accessory_chain_pasties",
        "鍊式胸貼（18+）",
        "ornamental chain-linked nipple pasties",
    ),
    (
        "adult_accessory_lace_anklets",
        "蕾絲腳踝飾（18+）",
        "delicate lace ankle adornments",
    ),
    (
        "adult_accessory_strappy_armbands",
        "多帶手臂環（18+）",
        "strappy upper-arm bands",
    ),
    (
        "adult_accessory_crystal_body_gems",
        "水晶人體藝術貼飾（18+）",
        "ornamental crystal body gems",
    ),
    (
        "adult_accessory_satin_thigh_bows",
        "緞帶大腿蝴蝶結（18+）",
        "decorative satin thigh bows",
    ),
    (
        "adult_accessory_navel_chain",
        "珠寶肚臍穿孔鍊（18+）",
        "jeweled belly chain attached to a navel piercing",
    ),
    (
        "adult_accessory_sheer_elbow_gloves",
        "透膚肘長手套（18+）",
        "sheer elbow-length gloves",
    ),
)


OUTFIT_MATERIALS_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("faux_leather", "人造皮革", "faux-leather fabric"),
    ("knit", "針織", "knitted fabric"),
    ("nylon", "尼龍", "nylon fabric"),
    ("chiffon", "雪紡", "chiffon fabric"),
    ("fishnet", "漁網", "fishnet fabric"),
    ("sheer", "透膚", "sheer fabric"),
    ("patent_leather", "漆皮", "patent leather"),
    ("satin", "緞面", "satin fabric"),
    ("organza", "歐根紗", "organza fabric"),
    ("tulle", "薄紗網", "tulle fabric"),
    ("jersey_knit", "針織汗布", "jersey-knit fabric"),
    ("ribbed_knit", "羅紋針織", "ribbed knit fabric"),
    ("canvas", "帆布", "canvas fabric"),
    ("tweed", "花呢", "tweed fabric"),
    ("corduroy", "燈芯絨", "corduroy fabric"),
    ("fleece", "刷毛", "fleece fabric"),
    ("faux_fur", "人造毛皮", "faux-fur fabric"),
    ("sequined", "亮片布", "sequined fabric"),
    ("metallic_textile", "金屬光澤布", "metallic textile"),
    ("vinyl", "亮面 Vinyl／PVC 材質", "glossy vinyl fabric"),
    ("neoprene", "氯丁橡膠", "neoprene fabric"),
    ("bamboo_fiber", "竹纖維", "bamboo-fiber fabric"),
    ("ramie", "苧麻", "ramie fabric"),
    ("cashmere", "喀什米爾", "cashmere fabric"),
)


OUTFIT_MATERIALS_ADULT: Final[tuple[ClothingTagDefinition, ...]] = ()


OUTFIT_PALETTE_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("palette_black", "黑色", "black clothing palette"),
    ("palette_white", "白色", "white clothing palette"),
    ("palette_gray", "灰色", "gray clothing palette"),
    ("palette_silver", "銀色", "silver clothing palette"),
    ("palette_red", "紅色", "red clothing palette"),
    ("palette_burgundy", "酒紅色", "burgundy clothing palette"),
    ("palette_pink", "粉紅色", "pink clothing palette"),
    ("palette_orange", "橙色", "orange clothing palette"),
    ("palette_yellow", "黃色", "yellow clothing palette"),
    ("palette_gold", "金色", "gold clothing palette"),
    ("palette_green", "綠色", "green clothing palette"),
    ("palette_olive", "橄欖綠", "olive clothing palette"),
    ("palette_teal", "青綠色", "teal clothing palette"),
    ("palette_cyan", "青色", "cyan clothing palette"),
    ("palette_blue", "藍色", "blue clothing palette"),
    ("palette_navy", "海軍藍", "navy clothing palette"),
    ("palette_purple", "紫色", "purple clothing palette"),
    ("palette_brown", "棕色", "brown clothing palette"),
    ("palette_beige", "米色", "beige clothing palette"),
    ("palette_cream", "奶油白", "cream clothing palette"),
    ("palette_multicolored", "多色", "multicolored clothing palette"),
    ("palette_copper", "銅色", "copper clothing palette"),
    ("palette_bronze", "青銅色", "bronze clothing palette"),
    ("palette_rose_gold", "玫瑰金", "rose-gold clothing palette"),
)


OUTFIT_PALETTE_ADULT: Final[tuple[ClothingTagDefinition, ...]] = ()


# V12 semantic partitions keep the V11 source tuples intact while exposing
# author-facing wardrobe slots that can be rendered and randomized separately.
_OUTFIT_UPPER_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    OUTFIT_ARCHETYPE_SAFE[0:16]
)
_OUTFIT_LOWER_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    OUTFIT_ARCHETYPE_SAFE[16:35]
)
_OUTFIT_ONE_PIECE_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    *OUTFIT_ARCHETYPE_SAFE[35:46],
    OUTFIT_ARCHETYPE_SAFE[133],
    OUTFIT_ARCHETYPE_SAFE[135],
)
_OUTFIT_OUTERWEAR_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    *OUTFIT_ARCHETYPE_SAFE[46:57],
    OUTFIT_ARCHETYPE_SAFE[134],
)
_OUTFIT_BRA_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    OUTFIT_ARCHETYPE_SAFE[57:65]
)
_OUTFIT_UNDERWEAR_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    *OUTFIT_ARCHETYPE_SAFE[65:79],
    *OUTFIT_ARCHETYPE_SAFE[94:98],
)
_OUTFIT_SLEEPWEAR_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    OUTFIT_ARCHETYPE_SAFE[79:89]
)
_OUTFIT_UNIFORM_SPORT_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    *OUTFIT_ARCHETYPE_SAFE[89:94],
    *OUTFIT_ARCHETYPE_SAFE[98:104],
)
_OUTFIT_SWIMWEAR_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    OUTFIT_ARCHETYPE_SAFE[104:133]
)


OUTFIT_UPPER_SAFE: Final[tuple[ClothingTagDefinition, ...]] = _OUTFIT_UPPER_SAFE_SOURCE


OUTFIT_LOWER_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    *_OUTFIT_LOWER_SAFE_SOURCE,
    ("capri_pants", "七分褲", "capri pants"),
    ("palazzo_pants", "寬管褲", "palazzo pants"),
    ("mini_skirt", "迷你裙", "miniskirt"),
    ("maxi_skirt", "及踝長裙", "maxi skirt"),
    ("layered_skirt", "層疊裙", "layered skirt"),
)


OUTFIT_ONE_PIECE_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    *_OUTFIT_ONE_PIECE_SAFE_SOURCE,
    ("qipao_dress", "旗袍洋裝", "qipao dress"),
    ("pinafore_dress", "吊帶裙", "pinafore dress"),
    ("asymmetrical_dress", "不對稱洋裝", "asymmetrical dress"),
)


OUTFIT_OUTERWEAR_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    *_OUTFIT_OUTERWEAR_SAFE_SOURCE,
    ("pea_coat", "雙排扣短大衣", "pea coat"),
    ("capelet", "小披肩", "capelet"),
    ("duster_coat", "長版防塵外套", "duster coat"),
    ("bolero_jacket", "短版小外套", "bolero jacket"),
)


OUTFIT_BRA_SAFE: Final[tuple[ClothingTagDefinition, ...]] = _OUTFIT_BRA_SAFE_SOURCE


OUTFIT_UNDERWEAR_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    *_OUTFIT_UNDERWEAR_SAFE_SOURCE,
    ("brazilian_briefs", "巴西式內褲", "Brazilian briefs"),
    ("seamless_boxer_briefs", "無痕貼身四角褲", "seamless boxer briefs"),
    ("woven_boxers", "梭織四角褲", "woven boxer shorts"),
    ("long_leg_boxer_briefs", "長版貼身四角褲", "long-leg boxer briefs"),
    ("performance_briefs", "運動快乾三角內褲", "moisture-wicking athletic briefs"),
    ("contour_pouch_trunks", "囊袋型短版四角褲", "contour-pouch trunks"),
)


OUTFIT_SLEEPWEAR_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    *_OUTFIT_SLEEPWEAR_SAFE_SOURCE,
    ("matching_pajama_set", "成套睡衣", "matching pajama set"),
    ("sleep_romper", "連身短睡衣", "sleep romper"),
    ("sleep_robe_set", "睡袍套裝", "sleep robe set"),
    ("thermal_sleepwear", "保暖睡衣", "thermal sleepwear"),
    ("silk_pajama_set", "絲質成套睡衣", "silk pajama set"),
    ("sleep_tunic", "長版睡衣上衣", "sleep tunic"),
)


OUTFIT_UNIFORM_SPORT_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    *_OUTFIT_UNIFORM_SPORT_SAFE_SOURCE,
    ("martial_arts_uniform", "武術訓練服", "martial arts training uniform"),
    ("school_uniform", "學院制服", "academy uniform"),
    ("military_uniform", "軍裝制服", "military dress uniform"),
    ("medical_scrubs", "醫療刷手服", "medical scrubs"),
    ("flight_suit", "飛行服", "flight suit"),
)


OUTFIT_SWIMWEAR_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    *_OUTFIT_SWIMWEAR_SAFE_SOURCE,
    ("long_sleeve_swimsuit", "長袖連身泳裝", "long-sleeve one-piece swimsuit"),
    ("full_body_surf_suit", "連身衝浪防寒衣", "full-body surf suit"),
    ("crossback_swimsuit", "交叉背帶泳裝", "crossback swimsuit"),
)


_OUTFIT_BRA_ADULT_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    OUTFIT_ARCHETYPE_ADULT[6],
    OUTFIT_ARCHETYPE_ADULT[7],
    OUTFIT_ARCHETYPE_ADULT[14],
    OUTFIT_ARCHETYPE_ADULT[15],
)
OUTFIT_BRA_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    *_OUTFIT_BRA_ADULT_SOURCE,
    ("adult_shelf_bra", "下托式開胸內衣（18+）", "shelf bra"),
    ("adult_quarter_cup_bra", "四分之一罩杯內衣（18+）", "quarter-cup bra"),
    ("adult_strappy_open_cup_bra", "綁帶開杯內衣（18+）", "strappy open-cup bra"),
    ("adult_sheer_bra", "透膚內衣（18+）", "sheer bra"),
)
OUTFIT_UNDERWEAR_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    OUTFIT_ARCHETYPE_ADULT[17:25]
)
OUTFIT_SLEEPWEAR_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    OUTFIT_ARCHETYPE_ADULT[3],
    *OUTFIT_ARCHETYPE_ADULT[25:32],
)
_OUTFIT_SWIMWEAR_ADULT_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    *OUTFIT_ARCHETYPE_ADULT[0:2],
    *OUTFIT_ARCHETYPE_ADULT[32:34],
    OUTFIT_ARCHETYPE_ADULT[45],
)
_SWIMWEAR_COVERUP_ADULT_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    ACCESSORIES_ADULT[1:3]
)
OUTFIT_SWIMWEAR_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    *_OUTFIT_SWIMWEAR_ADULT_SOURCE,
    ("adult_mesh_bikini", "網紗比基尼（18+）", "mesh bikini"),
    ("adult_sheer_bikini", "透膚比基尼（18+）", "sheer bikini"),
    ("adult_pearl_string_bikini", "珍珠細帶比基尼（18+）", "pearl-string bikini"),
    ("adult_open_cup_swimsuit", "開杯泳裝（18+）", "open-cup swimsuit"),
    *_SWIMWEAR_COVERUP_ADULT_SOURCE,
    (
        "adult_strappy_plunge_swimsuit",
        "深V綁帶連身泳裝（18+）",
        "strappy plunge one-piece swimsuit",
    ),
    (
        "adult_mesh_wrap_swimsuit",
        "網紗纏繞連身泳裝（18+）",
        "mesh wrap one-piece swimsuit",
    ),
    (
        "adult_cutout_halter_swimsuit",
        "挖空掛脖連身泳裝（18+）",
        "cutout halter one-piece swimsuit",
    ),
    (
        "adult_strappy_high_leg_bikini",
        "綁帶高衩比基尼（18+）",
        "strappy high-leg bikini",
    ),
    (
        "adult_sheer_swim_dress",
        "透膚泳裙（18+）",
        "sheer swim dress",
    ),
)
_OUTFIT_LINGERIE_ADULT_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    OUTFIT_ARCHETYPE_ADULT[2],
    *OUTFIT_ARCHETYPE_ADULT[4:6],
    *OUTFIT_ARCHETYPE_ADULT[8:14],
    OUTFIT_ARCHETYPE_ADULT[16],
    *OUTFIT_ARCHETYPE_ADULT[34:45],
    *OUTFIT_ARCHETYPE_ADULT[46:48],
)
OUTFIT_LINGERIE_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    *_OUTFIT_LINGERIE_ADULT_SOURCE,
    (
        "adult_satin_strappy_lingerie_set",
        "緞面綁帶情趣內衣套組（18+）",
        "satin strappy lingerie set",
    ),
)


_ACCESSORIES_HEAD_HAIR_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    *ACCESSORIES_SAFE[0:11],
    *ACCESSORIES_SAFE[34:37],
)
_ACCESSORIES_FACE_NECK_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    *ACCESSORIES_SAFE[11:13],
    ACCESSORIES_SAFE[37],
    ACCESSORIES_SAFE[39],
)
_ACCESSORIES_HAND_ARM_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    ACCESSORIES_SAFE[13],
    *ACCESSORIES_SAFE[15:19],
    ACCESSORIES_SAFE[38],
)
_ACCESSORIES_WAIST_BODY_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    ACCESSORIES_SAFE[14],
    *ACCESSORIES_SAFE[19:22],
)
_ACCESSORIES_BAGS_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    ACCESSORIES_SAFE[22:28]
)
_HOSIERY_STYLE_SAFE_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    ACCESSORIES_SAFE[28:34]
)


ACCESSORIES_HEAD_HAIR_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    *_ACCESSORIES_HEAD_HAIR_SAFE_SOURCE,
    ("flower_crown", "花冠", "flower crown"),
    ("decorative_veil", "裝飾頭紗", "decorative head veil"),
)
ACCESSORIES_FACE_NECK_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    *_ACCESSORIES_FACE_NECK_SAFE_SOURCE,
    ("silk_neck_scarf", "絲質頸巾", "silk neck scarf"),
    ("cravat", "領巾", "cravat"),
    ("face_veil", "面紗", "face veil"),
    ("neck_torque", "金屬頸環", "rigid metal torc necklace"),
)
ACCESSORIES_HAND_ARM_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    *_ACCESSORIES_HAND_ARM_SAFE_SOURCE,
    ("armlet", "上臂環", "decorative armlet"),
    ("hand_chain", "手鍊戒指一體鍊", "hand-chain jewelry"),
)
ACCESSORIES_WAIST_BODY_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    *_ACCESSORIES_WAIST_BODY_SAFE_SOURCE,
    ("obi_belt", "寬版腰封", "wide obi belt"),
    ("utility_body_harness", "工具背帶", "utility body harness"),
    ("decorative_body_sash", "裝飾斜掛帶", "decorative body sash"),
    ("hip_scarf", "臀腰圍巾", "decorative hip scarf"),
)
ACCESSORIES_BAGS_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    *_ACCESSORIES_BAGS_SAFE_SOURCE,
    ("waist_bag", "隨身腰包", "waist bag"),
    ("tote_bag", "托特包", "tote bag"),
)
HOSIERY_STYLE_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    *_HOSIERY_STYLE_SAFE_SOURCE,
    ("cable_knit_socks", "麻花針織襪", "cable-knit socks"),
    ("compression_socks", "壓縮襪", "compression socks"),
)
_HOSIERY_STYLE_ADULT_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    ACCESSORIES_ADULT[3:8]
)
HOSIERY_STYLE_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    *_HOSIERY_STYLE_ADULT_SOURCE,
    (
        "adult_hosiery_stay_up_stockings",
        "自黏式大腿絲襪（18+）",
        "stay-up thigh-high stockings",
    ),
    (
        "adult_hosiery_cuban_heel_stockings",
        "古巴跟絲襪（18+）",
        "Cuban-heel stockings",
    ),
    (
        "adult_hosiery_wet_look_tights",
        "濕亮質感褲襪（18+）",
        "wet-look tights",
    ),
)
_ACCESSORIES_INTIMATE_ADULT_SOURCE: Final[tuple[ClothingTagDefinition, ...]] = (
    ACCESSORIES_ADULT[0],
    *ACCESSORIES_ADULT[8:32],
)
ACCESSORIES_INTIMATE_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    *_ACCESSORIES_INTIMATE_ADULT_SOURCE,
    (
        "adult_accessory_jeweled_thigh_chain",
        "珠寶大腿鍊（18+）",
        "jeweled thigh chain",
    ),
    (
        "adult_accessory_strappy_waist_hip_harness",
        "綁帶腰胯束帶（18+）",
        "strappy waist-and-hip harness",
    ),
    (
        "adult_accessory_ornamental_body_cage",
        "裝飾籠狀身體束帶（18+）",
        "ornamental body-cage harness",
    ),
    (
        "adult_accessory_jeweled_waist_chain",
        "珠寶腰鍊（18+）",
        "jeweled waist chain",
    ),
    (
        "adult_accessory_lace_body_harness",
        "蕾絲身體束帶（18+）",
        "lace body harness",
    ),
    (
        "adult_accessory_satin_wrist_cuffs",
        "緞面腕環（18+）",
        "satin wrist cuffs",
    ),
    (
        "adult_accessory_crystal_thigh_garter",
        "水晶大腿環（18+）",
        "crystal thigh garter",
    ),
)


HOSIERY_LENGTH_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("hosiery_ankle_length", "踝襪長度", "ankle-length hosiery"),
    ("hosiery_quarter_length", "短筒襪長度", "quarter-length hosiery"),
    ("hosiery_crew_length", "中筒襪長度", "crew-length hosiery"),
    ("hosiery_mid_calf_length", "小腿中段長度", "mid-calf hosiery"),
    ("hosiery_knee_high_length", "及膝長度", "knee-high hosiery"),
    ("hosiery_over_knee_length", "過膝長度", "over-the-knee hosiery"),
    ("hosiery_thigh_high_length", "大腿高長度", "thigh-high hosiery"),
    ("hosiery_waist_high_length", "及腰長度", "waist-high hosiery"),
)


FOOTWEAR_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("sneakers", "休閒運動鞋", "casual sneakers"),
    ("running_shoes", "跑鞋", "running shoes"),
    ("loafers", "樂福鞋", "loafers"),
    ("oxford_shoes", "牛津鞋", "Oxford shoes"),
    ("ballet_flats", "芭蕾平底鞋", "ballet flats"),
    ("pumps", "包頭高跟鞋", "classic pumps"),
    ("stiletto_heels", "細跟高跟鞋", "stiletto heels"),
    ("block_heels", "粗跟鞋", "block heels"),
    ("platform_shoes", "厚底鞋", "platform shoes"),
    ("ankle_boots", "短靴", "ankle boots"),
    ("knee_high_boots", "及膝長靴", "knee-high boots"),
    ("thigh_high_boots", "過膝長靴", "thigh-high boots"),
    ("combat_boots", "戰鬥靴", "combat boots"),
    ("hiking_boots", "登山靴", "hiking boots"),
    ("riding_boots", "馬術長靴", "riding boots"),
    ("rain_boots", "雨靴", "rain boots"),
    ("flat_sandals", "平底涼鞋", "flat sandals"),
    ("strappy_sandals", "繫帶涼鞋", "strappy sandals"),
    ("flip_flops", "夾腳拖鞋", "flip-flops"),
    ("house_slippers", "室內拖鞋", "house slippers"),
    ("clogs", "木底鞋", "clogs"),
    ("mary_janes", "瑪莉珍鞋", "Mary Jane shoes"),
    ("moccasins", "莫卡辛鞋", "moccasins"),
    ("espadrilles", "草編底鞋", "espadrilles"),
)


FOOTWEAR_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    (
        "adult_footwear_platform_stilettos",
        "情趣厚底細跟鞋（18+）",
        "fetish platform stilettos",
    ),
    (
        "adult_footwear_thigh_high_stiletto_boots",
        "情趣過膝細跟靴（18+）",
        "thigh-high stiletto boots",
    ),
    (
        "adult_footwear_lace_up_thigh_boots",
        "情趣綁帶過膝靴（18+）",
        "lace-up thigh-high boots",
    ),
    ("adult_footwear_ballet_heels", "芭蕾高跟鞋（18+）", "ballet heels"),
    (
        "adult_footwear_clear_platform_heels",
        "透明厚底高跟鞋（18+）",
        "clear platform heels",
    ),
    (
        "adult_footwear_ankle_cuff_heels",
        "腳踝環帶高跟鞋（18+）",
        "ankle-cuff heels",
    ),
    (
        "adult_footwear_chain_heel_sandals",
        "鍊飾高跟涼鞋（18+）",
        "chain-detailed high-heel sandals",
    ),
    (
        "adult_footwear_open_toe_thigh_boots",
        "露趾過膝長靴（18+）",
        "open-toe thigh-high boots",
    ),
)


ADULT_TOYS: Final[tuple[ClothingTagDefinition, ...]] = (
    ("adult_toy_bullet_vibrator", "子彈型震動器（18+）", "bullet vibrator"),
    ("adult_toy_wand_vibrator", "按摩棒型震動器（18+）", "wand vibrator"),
    ("adult_toy_rabbit_vibrator", "兔型震動器（18+）", "rabbit vibrator"),
    ("adult_toy_suction_stimulator", "吸吮式刺激器（18+）", "suction stimulator"),
    ("adult_toy_wearable_vibrator", "穿戴式震動器（18+）", "wearable vibrator"),
    ("adult_toy_silicone_dildo", "矽膠假陽具（18+）", "silicone dildo"),
    ("adult_toy_glass_dildo", "玻璃假陽具（18+）", "glass dildo"),
    ("adult_toy_double_ended_dildo", "雙頭假陽具（18+）", "double-ended dildo"),
    (
        "adult_toy_strap_on_harness",
        "穿戴式假陽具（18+）",
        "strap-on dildo with harness",
    ),
    ("adult_toy_butt_plug", "肛塞（18+）", "butt plug"),
    ("adult_toy_jeweled_butt_plug", "珠寶肛塞（18+）", "jeweled butt plug"),
    ("adult_toy_inflatable_butt_plug", "充氣肛塞（18+）", "inflatable butt plug"),
    ("adult_toy_anal_beads", "肛珠（18+）", "anal beads"),
    ("adult_toy_feather_tickler", "羽毛搔癢棒（18+）", "feather tickler"),
    ("adult_toy_leather_paddle", "皮革拍板（18+）", "leather spanking paddle"),
    ("adult_toy_nipple_suction_cups", "乳頭吸杯（18+）", "nipple suction cups"),
)


OUTFIT_UPPER_STATE_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("upper_state_buttoned", "上衣扣好", "buttoned upper garment"),
    ("upper_state_zipped", "上衣拉鍊拉好", "zipped upper garment"),
    ("upper_state_tucked_in", "上衣紮入下身", "tucked-in upper garment"),
    ("upper_state_rolled_sleeves", "袖口捲起", "upper garment with rolled sleeves"),
)
OUTFIT_UPPER_STATE_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    ("upper_state_deeply_unbuttoned", "上衣大幅解扣（18+）", "deeply unbuttoned top"),
    (
        "upper_state_lifted_above_breasts",
        "上衣掀至胸部上方（18+）",
        "top lifted above the breasts",
    ),
    (
        "upper_state_pulled_aside",
        "上衣拉至一側（18+）",
        "top pulled aside to expose the breasts",
    ),
    (
        "upper_state_open_bare_torso",
        "上衣敞開露出軀幹（18+）",
        "open upper garment exposing the bare torso",
    ),
)


OUTFIT_LOWER_STATE_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("lower_state_fastened", "下身服裝扣好", "fastened lower garment"),
    ("lower_state_belted", "腰帶扣好", "securely belted lower garment"),
    ("lower_state_cuffed", "褲腳反摺", "lower garment with rolled cuffs"),
    ("lower_state_pressed", "下身服裝燙整", "neatly pressed lower garment"),
)
OUTFIT_LOWER_STATE_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    ("lower_state_unfastened", "下身服裝解扣（18+）", "unfastened lower garment"),
    (
        "lower_state_lowered_to_hips",
        "下身服裝褪至胯部（18+）",
        "lower garment lowered to the hips",
    ),
    (
        "lower_state_lowered_to_thighs",
        "下身服裝褪至大腿（18+）",
        "lower garment lowered to the thighs",
    ),
    (
        "lower_state_pulled_aside",
        "下身服裝拉至一側（18+）",
        "lower garment pulled aside",
    ),
)


OUTFIT_BRA_STATE_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("bra_state_fastened", "內衣扣好", "bra neatly fastened"),
    ("bra_state_straps_in_place", "肩帶位置整齊", "bra straps resting on both shoulders"),
    ("bra_state_cups_fitted", "罩杯貼合", "properly fitted bra cups"),
    ("bra_state_band_level", "下圍平整", "level bra band"),
)
OUTFIT_BRA_STATE_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    ("bra_state_unfastened", "內衣解扣（18+）", "unfastened bra"),
    ("bra_state_strap_slipped", "肩帶滑落（18+）", "bra strap slipped off one shoulder"),
    ("bra_state_cup_pulled_aside", "罩杯拉至一側（18+）", "bra cup pulled aside"),
    ("bra_state_lifted", "內衣掀至胸部上方（18+）", "bra lifted above the breasts"),
)


OUTFIT_UNDERWEAR_STATE_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("underwear_state_worn", "內褲穿戴整齊", "properly worn underwear"),
    ("underwear_state_flat_waistband", "褲頭平整", "flat underwear waistband"),
    ("underwear_state_centered", "內褲位置端正", "properly centered underwear"),
    ("underwear_state_seamless", "內褲線條平順", "smooth underwear lines"),
)
OUTFIT_UNDERWEAR_STATE_ADULT: Final[tuple[ClothingTagDefinition, ...]] = (
    (
        "underwear_state_lowered_to_hips",
        "內褲褪至胯部（18+）",
        "underwear lowered to the hips",
    ),
    (
        "underwear_state_lowered_to_thighs",
        "內褲褪至大腿（18+）",
        "underwear lowered to the thighs",
    ),
    (
        "underwear_state_pulled_aside",
        "內褲襠部拉至一側（18+）",
        "underwear pulled aside at the crotch",
    ),
    (
        "underwear_state_one_leg_removed",
        "內褲褪離單腿（18+）",
        "underwear slipped off one leg",
    ),
)


def _assert_exact_partition(
    source: tuple[ClothingTagDefinition, ...],
    partitions: tuple[tuple[ClothingTagDefinition, ...], ...],
    *,
    name: str,
) -> None:
    flattened = tuple(item for partition in partitions for item in partition)
    if len(flattened) != len(set(flattened)):
        raise ValueError(f"{name} partition contains duplicate definitions")
    if len(flattened) != len(source) or set(flattened) != set(source):
        raise ValueError(f"{name} partition must cover its source exactly once")


def _assert_v12_catalog_integrity(
    catalogs: tuple[tuple[ClothingTagDefinition, ...], ...],
) -> None:
    definitions = tuple(item for catalog in catalogs for item in catalog)
    for field_index, field_name in ((0, "key"), (1, "label"), (2, "prompt")):
        values = tuple(item[field_index] for item in definitions)
        comparable = tuple(value.casefold() for value in values)
        if len(comparable) != len(set(comparable)):
            raise ValueError(f"V12 clothing {field_name} values must be globally unique")
    for key, label, prompt in definitions:
        if not key or not label or not prompt:
            raise ValueError("V12 clothing definitions cannot contain blank fields")
        if not any("\u3400" <= character <= "\u9fff" for character in label):
            raise ValueError(f"V12 clothing label must contain Traditional Chinese: {key}")
        if prompt != prompt.strip() or not prompt.isascii() or "," in prompt:
            raise ValueError(f"V12 clothing prompt must be one clean English fragment: {key}")


_assert_exact_partition(
    OUTFIT_ARCHETYPE_SAFE,
    (
        _OUTFIT_UPPER_SAFE_SOURCE,
        _OUTFIT_LOWER_SAFE_SOURCE,
        _OUTFIT_ONE_PIECE_SAFE_SOURCE,
        _OUTFIT_OUTERWEAR_SAFE_SOURCE,
        _OUTFIT_BRA_SAFE_SOURCE,
        _OUTFIT_UNDERWEAR_SAFE_SOURCE,
        _OUTFIT_SLEEPWEAR_SAFE_SOURCE,
        _OUTFIT_UNIFORM_SPORT_SAFE_SOURCE,
        _OUTFIT_SWIMWEAR_SAFE_SOURCE,
    ),
    name="OUTFIT_ARCHETYPE_SAFE",
)
_assert_exact_partition(
    OUTFIT_ARCHETYPE_ADULT,
    (
        _OUTFIT_BRA_ADULT_SOURCE,
        OUTFIT_UNDERWEAR_ADULT,
        OUTFIT_SLEEPWEAR_ADULT,
        _OUTFIT_SWIMWEAR_ADULT_SOURCE,
        _OUTFIT_LINGERIE_ADULT_SOURCE,
    ),
    name="OUTFIT_ARCHETYPE_ADULT",
)
_assert_exact_partition(
    ACCESSORIES_SAFE,
    (
        _ACCESSORIES_HEAD_HAIR_SAFE_SOURCE,
        _ACCESSORIES_FACE_NECK_SAFE_SOURCE,
        _ACCESSORIES_HAND_ARM_SAFE_SOURCE,
        _ACCESSORIES_WAIST_BODY_SAFE_SOURCE,
        _ACCESSORIES_BAGS_SAFE_SOURCE,
        _HOSIERY_STYLE_SAFE_SOURCE,
    ),
    name="ACCESSORIES_SAFE",
)
_assert_exact_partition(
    ACCESSORIES_ADULT,
    (
        _SWIMWEAR_COVERUP_ADULT_SOURCE,
        _HOSIERY_STYLE_ADULT_SOURCE,
        _ACCESSORIES_INTIMATE_ADULT_SOURCE,
    ),
    name="ACCESSORIES_ADULT",
)


_V12_CATALOGS: Final[tuple[tuple[ClothingTagDefinition, ...], ...]] = (
    OUTFIT_UPPER_SAFE,
    OUTFIT_LOWER_SAFE,
    OUTFIT_ONE_PIECE_SAFE,
    OUTFIT_OUTERWEAR_SAFE,
    OUTFIT_BRA_SAFE,
    OUTFIT_UNDERWEAR_SAFE,
    OUTFIT_SLEEPWEAR_SAFE,
    OUTFIT_UNIFORM_SPORT_SAFE,
    OUTFIT_SWIMWEAR_SAFE,
    OUTFIT_BRA_ADULT,
    OUTFIT_UNDERWEAR_ADULT,
    OUTFIT_SLEEPWEAR_ADULT,
    OUTFIT_SWIMWEAR_ADULT,
    OUTFIT_LINGERIE_ADULT,
    ACCESSORIES_HEAD_HAIR_SAFE,
    ACCESSORIES_FACE_NECK_SAFE,
    ACCESSORIES_HAND_ARM_SAFE,
    ACCESSORIES_WAIST_BODY_SAFE,
    ACCESSORIES_BAGS_SAFE,
    HOSIERY_STYLE_SAFE,
    HOSIERY_STYLE_ADULT,
    ACCESSORIES_INTIMATE_ADULT,
    HOSIERY_LENGTH_SAFE,
    FOOTWEAR_SAFE,
    FOOTWEAR_ADULT,
    ADULT_TOYS,
    OUTFIT_UPPER_STATE_SAFE,
    OUTFIT_UPPER_STATE_ADULT,
    OUTFIT_LOWER_STATE_SAFE,
    OUTFIT_LOWER_STATE_ADULT,
    OUTFIT_BRA_STATE_SAFE,
    OUTFIT_BRA_STATE_ADULT,
    OUTFIT_UNDERWEAR_STATE_SAFE,
    OUTFIT_UNDERWEAR_STATE_ADULT,
)
_assert_v12_catalog_integrity(_V12_CATALOGS)
