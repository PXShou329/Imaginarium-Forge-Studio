"""Curated clothing and accessory definitions for general character design.

Each tuple contains a stable key, a Traditional Chinese label, and one English
prompt fragment. Definitions remain independent from the prompt service.
"""

from typing import Final

type ClothingTagDefinition = tuple[str, str, str]


OUTFIT_UPPER_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
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
)


OUTFIT_LOWER_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("trousers", "長褲", "trousers"),
    ("jeans", "牛仔褲", "jeans"),
    ("shorts", "短褲", "shorts"),
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
    ("capri_pants", "七分褲", "capri pants"),
    ("palazzo_pants", "寬管褲", "palazzo pants"),
    ("mini_skirt", "迷你裙", "miniskirt"),
    ("maxi_skirt", "及踝長裙", "maxi skirt"),
    ("layered_skirt", "層疊裙", "layered skirt"),
)


OUTFIT_ONE_PIECE_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
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
    ("utility_jumpsuit", "工裝連身服", "utility jumpsuit"),
    ("wrap_dress", "裹身洋裝", "wrap dress"),
    ("qipao_dress", "旗袍洋裝", "qipao dress"),
    ("pinafore_dress", "吊帶裙", "pinafore dress"),
    ("asymmetrical_dress", "不對稱洋裝", "asymmetrical dress"),
)


OUTFIT_OUTERWEAR_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
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
    ("poncho", "斗篷式披肩", "poncho"),
    ("pea_coat", "雙排扣短大衣", "pea coat"),
    ("capelet", "小披肩", "capelet"),
    ("duster_coat", "長版防塵外套", "duster coat"),
    ("bolero_jacket", "短版小外套", "bolero jacket"),
)


OUTFIT_SLEEPWEAR_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
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
    ("matching_pajama_set", "成套睡衣", "matching pajama set"),
    ("sleep_romper", "連身短睡衣", "sleep romper"),
    ("sleep_robe_set", "睡袍套裝", "sleep robe set"),
    ("thermal_sleepwear", "保暖睡衣", "thermal sleepwear"),
    ("silk_pajama_set", "絲質成套睡衣", "silk pajama set"),
    ("sleep_tunic", "長版睡衣上衣", "sleep tunic"),
)


OUTFIT_UNIFORM_SPORT_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("dress_shirt", "正裝襯衫", "dress shirt"),
    ("dress_pants", "西裝褲", "dress pants"),
    ("chinos", "卡其褲", "chinos"),
    ("suit_jacket", "西裝上衣", "suit jacket"),
    ("undershirt", "汗衫", "undershirt"),
    ("sports_shirt", "運動上衣", "sports shirt"),
    ("sports_shorts", "運動短褲", "sports shorts"),
    ("compression_shirt", "壓縮衣", "compression shirt"),
    ("compression_tights", "壓縮褲", "compression tights"),
    ("track_pants", "運動套裝長褲", "track pants"),
    ("tracksuit", "運動套裝", "tracksuit"),
    ("martial_arts_uniform", "武術訓練服", "martial arts training uniform"),
    ("school_uniform", "學院制服", "academy uniform"),
    ("military_uniform", "軍裝制服", "military dress uniform"),
    ("medical_scrubs", "醫療刷手服", "medical scrubs"),
    ("flight_suit", "飛行服", "flight suit"),
)


OUTFIT_SWIMWEAR_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("swim_trunks", "泳褲", "swim trunks"),
    ("board_shorts", "衝浪褲", "board shorts"),
    ("jammer", "五分競泳褲", "swim jammer"),
    ("rash_guard", "防磨／防曬泳衣", "rash guard"),
    ("one_piece_swimsuit", "一件式泳裝", "one-piece swimsuit"),
    ("swim_dress", "泳裙", "swim dress"),
    ("competitive_swimsuit", "競泳泳裝", "competitive swimsuit"),
    ("zip_front_swimsuit", "前拉鍊泳裝", "zip-front swimsuit"),
    ("racing_one_piece", "競速型一件式泳裝", "racing one-piece swimsuit"),
    ("long_sleeve_swimsuit", "長袖連身泳裝", "long-sleeve one-piece swimsuit"),
    ("full_body_surf_suit", "連身衝浪防寒衣", "full-body surf suit"),
    ("crossback_swimsuit", "交叉背帶泳裝", "crossback swimsuit"),
)


ACCESSORIES_HEAD_HAIR_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
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
    ("bucket_hat", "漁夫帽", "bucket hat"),
    ("newsboy_cap", "報童帽", "newsboy cap"),
    ("fascinator", "小禮帽頭飾", "fascinator headpiece"),
    ("flower_crown", "花冠", "flower crown"),
    ("decorative_veil", "裝飾頭紗", "decorative head veil"),
)


ACCESSORIES_FACE_NECK_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("tie", "領帶", "necktie"),
    ("bow_tie", "蝴蝶領結", "bow tie"),
    ("lapel_pin", "領針", "decorative lapel pin"),
    ("pocket_square", "口袋巾", "folded pocket square"),
    ("silk_neck_scarf", "絲質頸巾", "silk neck scarf"),
    ("cravat", "領巾", "cravat"),
    ("face_veil", "面紗", "face veil"),
    ("neck_torque", "金屬頸環", "rigid metal torc necklace"),
)


ACCESSORIES_HAND_ARM_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("bracelet", "手鍊", "bracelet"),
    ("fingerless_gloves", "無指手套", "fingerless gloves"),
    ("mittens", "連指手套", "mittens"),
    ("wristband", "運動護腕", "athletic wristband"),
    ("watch", "手錶", "wristwatch"),
    ("cufflinks", "袖扣", "ornate cufflinks"),
    ("armlet", "上臂環", "decorative armlet"),
    ("hand_chain", "手鍊戒指一體鍊", "hand-chain jewelry"),
)


ACCESSORIES_WAIST_BODY_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("anklet", "腳鍊", "anklet"),
    ("belt", "腰帶", "belt"),
    ("waist_chain", "腰鍊", "waist chain"),
    ("suspenders", "吊褲帶", "suspenders"),
    ("obi_belt", "寬版腰封", "wide obi belt"),
    ("utility_body_harness", "工具背帶", "utility body harness"),
    ("decorative_body_sash", "裝飾斜掛帶", "decorative body sash"),
    ("hip_scarf", "臀腰圍巾", "decorative hip scarf"),
)


ACCESSORIES_BAGS_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("backpack", "後背包", "backpack"),
    ("handbag", "手提包", "handbag"),
    ("shoulder_bag", "肩背包", "shoulder bag"),
    ("crossbody_bag", "斜背包", "crossbody bag"),
    ("clutch", "手拿包", "clutch bag"),
    ("coin_purse", "零錢包", "coin purse"),
    ("waist_bag", "隨身腰包", "waist bag"),
    ("tote_bag", "托特包", "tote bag"),
)


HOSIERY_STYLE_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("socks", "一般襪子", "socks"),
    ("stockings", "長絲襪／長襪", "stockings"),
    ("pantyhose", "薄型褲襪", "pantyhose"),
    ("tights", "厚型褲襪", "tights"),
    ("leg_warmers", "腿套", "leg warmers"),
    ("cable_knit_socks", "麻花針織襪", "cable-knit socks"),
    ("compression_socks", "壓縮襪", "compression socks"),
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


OUTFIT_UPPER_STATE_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("upper_state_buttoned", "上衣扣好", "buttoned upper garment"),
    ("upper_state_zipped", "上衣拉鍊拉好", "zipped upper garment"),
    ("upper_state_tucked_in", "上衣紮入下身", "tucked-in upper garment"),
    ("upper_state_rolled_sleeves", "袖口捲起", "upper garment with rolled sleeves"),
)


OUTFIT_LOWER_STATE_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("lower_state_fastened", "下身服裝扣好", "fastened lower garment"),
    ("lower_state_belted", "腰帶扣好", "securely belted lower garment"),
    ("lower_state_cuffed", "褲腳反摺", "lower garment with rolled cuffs"),
    ("lower_state_pressed", "下身服裝燙整", "neatly pressed lower garment"),
)


OUTFIT_MATERIALS_SAFE: Final[tuple[ClothingTagDefinition, ...]] = (
    ("faux_leather", "人造皮革", "faux-leather fabric"),
    ("knit", "針織", "knitted fabric"),
    ("nylon", "尼龍", "nylon fabric"),
    ("chiffon", "雪紡", "chiffon fabric"),
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
    ("neoprene", "氯丁橡膠", "neoprene fabric"),
    ("bamboo_fiber", "竹纖維", "bamboo-fiber fabric"),
    ("ramie", "苧麻", "ramie fabric"),
    ("cashmere", "喀什米爾", "cashmere fabric"),
)


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


def _assert_catalog_integrity(
    catalogs: tuple[tuple[ClothingTagDefinition, ...], ...],
) -> None:
    definitions = tuple(item for catalog in catalogs for item in catalog)
    for field_index, field_name in ((0, "key"), (1, "label"), (2, "prompt")):
        values = tuple(item[field_index].casefold() for item in definitions)
        if len(values) != len(set(values)):
            raise ValueError(f"Clothing {field_name} values must be globally unique")
    for key, label, prompt in definitions:
        if not key or not label or not prompt:
            raise ValueError("Clothing definitions cannot contain blank fields")
        if not any("\u3400" <= character <= "\u9fff" for character in label):
            raise ValueError(f"Clothing label must contain Traditional Chinese: {key}")
        if prompt != prompt.strip() or not prompt.isascii() or "," in prompt:
            raise ValueError(f"Clothing prompt must be one clean English fragment: {key}")


_CATALOGS: Final[tuple[tuple[ClothingTagDefinition, ...], ...]] = (
    OUTFIT_UPPER_SAFE,
    OUTFIT_LOWER_SAFE,
    OUTFIT_ONE_PIECE_SAFE,
    OUTFIT_OUTERWEAR_SAFE,
    OUTFIT_SLEEPWEAR_SAFE,
    OUTFIT_UNIFORM_SPORT_SAFE,
    OUTFIT_SWIMWEAR_SAFE,
    ACCESSORIES_HEAD_HAIR_SAFE,
    ACCESSORIES_FACE_NECK_SAFE,
    ACCESSORIES_HAND_ARM_SAFE,
    ACCESSORIES_WAIST_BODY_SAFE,
    ACCESSORIES_BAGS_SAFE,
    HOSIERY_STYLE_SAFE,
    HOSIERY_LENGTH_SAFE,
    FOOTWEAR_SAFE,
    OUTFIT_UPPER_STATE_SAFE,
    OUTFIT_LOWER_STATE_SAFE,
    OUTFIT_MATERIALS_SAFE,
    OUTFIT_PALETTE_SAFE,
)
_assert_catalog_integrity(_CATALOGS)
