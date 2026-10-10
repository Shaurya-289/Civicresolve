import csv

categories = {
    "road_damage": {
        "en": ["Small pothole on {l}, mildly bumpy but no danger yet.", "Deep potholes on {l} have damaged scooters for {d}, repair pls.", "Urgent: huge pothole at {l} near {m}; a rider fell this morning."],
        "hi": ["{l} की गली में हल्का गड्ढा है, बस थोड़ी असुविधा होती है।", "{l} पर बड़े गड्ढे {d} से हैं, रोज़ वाहन झटके खा रहे हैं।", "तुरंत मदद चाहिए, {l} के पास गहरा गड्ढा है और बाइक गिर रही हैं।"],
        "hing": ["{l} ki lane mein chota pothole hai, minor issue hai plz patch kar do.", "{l} ki road bht kharab hai {d} se, vehicles zigzag chal rahe hain pls fix.", "Urgent plz, {l} par deep gadda hai near {m}, ek bike gir chuki hai."]
    },
    "streetlight": {
        "en": ["One lamp near {l} flickers occasionally; please check it when possible.", "Three streetlights on {l} have been off for {d}; walking is difficult.", "Urgent: complete darkness at {l} near {m}; women and students feel unsafe."],
        "hi": ["{l} के पास एक स्ट्रीट लाइट टिमटिमा रही है, कृपया देख लें।", "{l} की कई स्ट्रीट लाइट {d} से बंद हैं, पैदल चलना मुश्किल है।", "तुरंत लाइट ठीक करें, {l} में {m} के पास बहुत अंधेरा है।"],
        "hing": ["{l} ki ek light blink kar rahi hai, koi danger nahi but pls check.", "{l} ki streetlights {d} se band hain, raat ko torch use karni padti hai.", "Urgent: {l} near {m} pura dark hai, students ko unsafe feel hota hai."]
    },
    "drainage": {
        "en": ["Drain cover near {l} is clogged with leaves; water drains slowly.", "Drain at {l} has overflowed for {d}, leaving dirty water on the lane.", "Urgent health hazard: sewage is overflowing at {l} near {m}."],
        "hi": ["{l} के पास नाली में पत्ते फँसे हैं, पानी धीरे निकल रहा है।", "{l} की नाली {d} से उफन रही है, गंदा पानी सड़क पर है।", "तुरंत सफाई चाहिए, {l} में {m} के पास सीवेज बह रहा है।"],
        "hing": ["{l} ki naali mein patte jam gaye hain, halka pani rukta hai pls clean.", "{l} ki naali {d} se choke hai aur ganda pani road pe aa raha hai.", "Emergency pls, {l} near {m} sewage overflow ho raha hai, health risk hai."]
    },
    "water_leak": {
        "en": ["A small pipe drip near {l} is wasting water slowly, please inspect.", "Water pipe at {l} has been leaking for {d}, wasting clean water.", "Urgent: major pipeline burst at {l} near {m}; road is dangerously slippery."],
        "hi": ["{l} के पास पाइप से हल्का पानी टपक रहा है, कृपया जाँच करें।", "{l} की पाइपलाइन {d} से लीक है, बहुत पानी बर्बाद हो रहा है।", "तुरंत मरम्मत करें, {l} में {m} के पास पाइप फट गया है।"],
        "hing": ["{l} par pipe se halka drip ho raha hai, chota leak hai pls dekh lo.", "{l} ki pipeline {d} se leak hai, pani waste aur road slippery ho rahi.", "Urgent plz, {l} near {m} main pipe burst hai aur pani flood ho raha."]
    },
    "waste": {
        "en": ["A small pile of litter near {l} needs collection; the lane is otherwise fine.", "Garbage pile at {l} has not been collected for {d} and smells bad.", "Urgent health issue: rotting waste at {l} near {m} is attracting animals."],
        "hi": ["{l} के पास थोड़ा कचरा पड़ा है, अगली सफाई में उठा लें।", "{l} में कचरा {d} से नहीं उठा, बदबू बहुत आ रही है।", "तुरंत कचरा उठाएँ, {l} में {m} के पास स्वास्थ्य खतरा है।"],
        "hing": ["{l} ke paas thoda litter pada hai, next round mein utha do pls.", "{l} mein kachra {d} se pada hai aur bht smell aa rahi hai.", "Urgent cleanup, {l} near {m} rotting waste se health hazard ho gaya."]
    }
}
localities = ["Malviya Nagar Sector 3", "Mansarovar Sector 7", "Vaishali Nagar", "Bagru main road", "Jagatpura", "Tonk Road", "C-Scheme", "Jhotwara", "Vidhyadhar Nagar", "Pratap Nagar", "Sodala", "Amer Road", "Bapu Nagar", "Sanganer", "Raja Park", "Civil Lines", "Muralipura", "Sitapura Industrial Area", "Shastri Nagar", "Adarsh Nagar", "Gopalpura bypass", "Nirman Nagar", "Ramganj Bazaar", "Kukas"]
durations = ["2 days", "5 din se", "one week", "10 din se", "2 weeks", "three days", "15 din se", "last Monday"]
landmarks = ["government school", "community hospital", "bus stop", "anganwadi", "market gate", "clinic", "metro station", "temple crossing"]
rows = []
for cat, copy in categories.items():
    for level, sev in enumerate(("low", "medium", "high")):
        for lang, count in (("en", 24), ("hing", 24), ("hi", 12)):
            for i in range(count):
                text = copy[lang][level].format(l=localities[i], d=durations[(i + level) % 8], m=landmarks[(i + level) % 8])
                suffix = f" Block {i + 1}." if lang != "hi" else f" गली नंबर {i + 1}।"
                rows.append((text + suffix, cat, sev))
assert len(rows) == 900
assert {s: sum(r[2] == s for r in rows) for s in ("low", "medium", "high")} == {"low": 300, "medium": 300, "high": 300}
assert len({r[0] for r in rows}) == 900
with open("civic_complaints_900.csv", "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    writer.writerow(("text", "category", "severity"))
    writer.writerows(rows)
