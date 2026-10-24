/* 萬安政策島 TD — 所有可調參數。其他檔案不得出現魔術數字。
   新增議題：ISSUES 加一筆（或編輯者在大廳表單新增）。新增動物：ANIMALS 加一筆，model 指到 animals.js 的外型。 */
export const CONFIG = {
  site: {
    name: '萬安政策島', sub: 'TD・守住市政府', tag: '非官方・網友自製', waveWord: '波攻擊',
    credit: '島嶼、人物與場景的做法參考 puma.taipei 的「HEART 政策島」互動頁，重新實作。',
    disclaimer: '這是網友自製的塔防小遊戲，不是台北市政府或任何競選團隊的官方內容。政策摘要整理自公開報導、僅供遊戲參考，細節以官方資訊為準；地標與建築只是象徵性迷你模型，議題、數值、動物與路線純屬遊戲設定，與任何真實人物或團體無關。',
  },
  election: { date: '2026-11-28', tz: 'Asia/Taipei', finalIssueId: 'final' },
  round: {
    seconds: 180, bossAt: 140, surgeAt: 158, surgeEvery: 1.5, surgePct: 0.35, surgeBelowApproval: 40, wipeAt: 176,
    startApproval: 100, approvalFloor: 1,
    baseSpawn: 26, bursts: [ { from: 3, to: 45, share: 0.35 }, { from: 55, to: 98, share: 0.35 }, { from: 106, to: 138, share: 0.30 } ],
    windShift: { minGap: 30, maxGap: 55, duration: 5, speedMul: 1.5 },
    raid: { minGap: 28, maxGap: 48, size: 5, chance: 0.7 },
    unitsPerSec: 0.9,          // 敵速 1.0 = 每秒走幾個世界單位（一座島直徑約 4）
    chantOn: 1.8, chantOff: 1.3,
    focusDmgMul: 3, focusCooldown: 1.4, ultCooldown: 45, ultPct: 0.6, ultSlow: 0.6, ultSlowTime: 3,
    projSpeed: 9,
  },
  // 居民（原本就住在島上的人）：平常在步道上散步，有敵人時會走過去勸退。
  residents: { perIsland: 7, speed: 0.6, engageRadius: 9, meleeRange: 0.45, dps: 9, maxPerTarget: 3, roles: ['citizen','elder','child','parent','officeworker','youth','cyclist','worker','hiker'] },
  // 英雄（登入者）：點島嶼就走過去，自動攻擊射程內的敵人。
  heroes: {
    chief:     { name: '里長伯',   icon: '🧓', color: 0x1f4e79, dmg: 16, rate: 0.55, range: 3.2, style: 'ball',  speed: 1.1, desc: '人面廣，中距離穩定輸出。' },
    driver:    { name: '公車司機', icon: '🚌', color: 0x2f6f9f, dmg: 10, rate: 0.3,  range: 3.0, style: 'bus',   speed: 1.2, desc: '班次密，連發。' },
    fireman:   { name: '消防員',   icon: '🚒', color: 0xc9452f, dmg: 18, rate: 0.7,  range: 2.8, style: 'water', slow: 0.5, slowTime: 1.6, speed: 1.1, desc: '水柱打到會減速。' },
    volunteer: { name: '志工媽媽', icon: '🧺', color: 0xc97d97, dmg: 9,  rate: 0.6,  range: 2.8, style: 'ball',  regen: 0.4, rally: 1.3, speed: 1.05, desc: '每秒回一點滿意度，身邊居民攻擊 +30%。' },
    engineer:  { name: '工程師',   icon: '💻', color: 0x4a3f8f, dmg: 22, rate: 1.0,  range: 3.2, style: 'zap',   chain: 3, speed: 1.0, desc: '台北通連鎖電擊，一次打 3 隻。' },
  },
  // 攻擊方動物（model: bird / frog / worm / lizard / mouse）
  animals: {
    bird:        { name: '青鳥',       model: 'bird',   color: 0x17a3b4, accent: 0x0d7281, hp: 40,   speed: 1.0,  bounty: 1, size: 1.0, leak: 4,  fly: true,  chants: ['護樹!!','出來面對','怒'] },
    frog:        { name: '青蛙',       model: 'frog',   color: 0x4caf50, accent: 0x2e7d32, hp: 95,   speed: 0.65, bounty: 1, size: 1.0, leak: 6,  hop: true,  chants: ['呱!!','呱呱呱','蛙~'] },
    parrot:      { name: '綠鸚鵡',     model: 'bird',   color: 0x6abf4b, accent: 0xd94a3a, hp: 60,   speed: 1.1,  bounty: 1, size: 0.9, leak: 4,  fly: true,  repeat: true, chants: ['複製貼上','同上','+1'] },
    caterpillar: { name: '綠毛蟲',     model: 'worm',   color: 0x7cb342, accent: 0x558b2f, hp: 240,  speed: 0.38, bounty: 1, size: 1.0, leak: 8,  chants: ['慢慢來','蠕動中','不急'] },
    chameleon:   { name: '變色龍',     model: 'lizard', color: 0x66bb6a, accent: 0xffb300, hp: 130,  speed: 0.9,  bounty: 1, size: 1.0, leak: 6,  shift: true, hairpin: [0.45, 1.5], chants: ['髮夾彎','我沒說過','立場不變'] },
    mouse:       { name: '鍵盤鼠',     model: 'mouse',  color: 0x9e9e9e, accent: 0x616161, hp: 22,   speed: 1.6,  bounty: 1, size: 0.7, leak: 1,  chants: ['已檢舉','笑死','洗起來','推'] },
    bigv:        { name: '側翼大 V',   model: 'bird',   color: 0x0e5f6b, accent: 0xf2c14e, hp: 950,  speed: 0.5,  bounty: 1, size: 2.0, leak: 25, fly: true,  boss: true, spawn: { type: 'mouse', every: 4 }, chants: ['下台!!','帶風向中','轉發一百次'] },
    frogking:    { name: '大青蛙王',   model: 'frog',   color: 0x2e7d32, accent: 0xf2c14e, hp: 1500, speed: 0.38, bounty: 1, size: 2.3, leak: 30, hop: true,  boss: true, spawn: { type: 'frog', every: 7 }, chants: ['呱!!!!','總攻擊','全軍出動'] },
  },
  // 12 座島 = 台北市 12 個行政區，軸座標依實際地理相對位置排列（上北下南、左西右東）。goal = 市政府所在的信義區。
  // landmark：該區的象徵地標（純裝飾）；政策建築另外依 policies 隨機散落。
  islands: [
    { id: 'beitou',   number: 1,  axial: [1, 0],  name: '北投區', ground: 'meadow',     landmark: 'hotspring' },
    { id: 'shilin',   number: 2,  axial: [0, 1],  name: '士林區', ground: 'lawn',       landmark: 'palace' },
    { id: 'neihu',    number: 3,  axial: [1, 1],  name: '內湖區', ground: 'lawn',       landmark: 'techcampus' },
    { id: 'datong',   number: 4,  axial: [-1, 2], name: '大同區', ground: 'courtyard',  landmark: 'oldstreet' },
    { id: 'zhongshan',number: 5,  axial: [0, 2],  name: '中山區', ground: 'street',     landmark: 'grandhotel' },
    { id: 'songshan', number: 6,  axial: [1, 2],  name: '松山區', ground: 'street',     landmark: 'airport' },
    { id: 'nangang',  number: 7,  axial: [2, 2],  name: '南港區', ground: 'gravel',     landmark: 'expo' },
    { id: 'wanhua',   number: 8,  axial: [-2, 3], name: '萬華區', ground: 'terracotta', landmark: 'temple' },
    { id: 'zhongzheng',number: 9, axial: [-1, 3], name: '中正區', ground: 'granite',    landmark: 'station' },
    { id: 'daan',     number: 10, axial: [0, 3],  name: '大安區', ground: 'garden',     landmark: 'park' },
    { id: 'xinyi',    number: 11, axial: [1, 3],  name: '信義區', ground: 'granite',    landmark: 'cityhall', goal: true },
    { id: 'wenshan',  number: 12, axial: [0, 4],  name: '文山區', ground: 'forest',     landmark: 'gondola' },
  ],
  // 敵人從河口登陸的區（四個方向），再沿步道與橋走向信義區市政府。
  landings: { west: { island: 'wanhua', name: '淡水河' }, north: { island: 'beitou', name: '關渡' }, east: { island: 'nangang', name: '基隆河' }, south: { island: 'wenshan', name: '景美溪' } },
  // 政策：每一筆是一棟可點擊的建築，沒指定 district 就依 id 雜湊隨機落在某座島。後台（共享資料庫 policies 集合）新增的會自動出現。
  // model 可用：housing nursery elderhome busstop dome techcampus school smartpole garden crane containers theater kiosk station crossing nightmarket hub card court bigtree
  policies: [
    { id: 'housing',   title: '社會住宅',       tag: '#居住',  model: 'housing',    summary: '持續興建與包租代管，讓年輕家庭租得起。', facts: ['社宅興辦與包租代管並行', '租金分級補貼'] },
    { id: 'childcare', title: '托育與育兒支持', tag: '#育兒',  model: 'nursery',    summary: '公共托育據點與育兒津貼，減輕年輕父母負擔。', facts: ['擴增公共托育名額', '生育獎勵與育兒津貼'] },
    { id: 'senior',    title: '敬老卡點數',     tag: '#敬老',  model: 'card',       summary: '敬老卡點數加碼，鼓勵長者走出家門。', facts: ['點數可用於交通與運動中心', '長照據點持續布建'] },
    { id: 'tpass',     title: 'TPASS 通勤月票', tag: '#交通',  model: 'busstop',    summary: '基北北桃通勤月票，公車捷運一票到底。', facts: ['月票整合公車、捷運、YouBike', '持續檢討路網'] },
    { id: 'dome',      title: '大巨蛋啟用',     tag: '#運動',  model: 'dome',       summary: '大巨蛋完工啟用，承辦職棒與大型賽事。', facts: ['國際賽事與演唱會場地', '周邊交通疏運計畫'], district: 'xinyi' },
    { id: 'techpark',  title: '北士科園區',     tag: '#產業',  model: 'techcampus', summary: '北投士林科技園區招商，引進科技總部。', facts: ['產業用地釋出', '國際企業進駐洽談'], district: 'beitou' },
    { id: 'bilingual', title: '雙語教育',       tag: '#教育',  model: 'school',     summary: '雙語實驗課程與外師，從小學到高中。', facts: ['雙語學校逐年增加', '數位學習載具'] },
    { id: 'taipeipass',title: '台北通',         tag: '#智慧',  model: 'smartpole',  summary: '一個 App 搞定市政服務、繳費與點數。', facts: ['整合市民卡功能', '智慧路燈與感測'] },
    { id: 'green',     title: '公園綠地與護樹', tag: '#環境',  model: 'garden',     summary: '增加鄰里公園與行道樹養護。', facts: ['樹木保護自治條例', '口袋公園'] },
    { id: 'renewal',   title: '都市更新',       tag: '#都更',  model: 'crane',      summary: '加速老屋重建與公辦都更。', facts: ['危老重建輔導', '公辦都更示範案'] },
    { id: 'youth',     title: '青年創業',       tag: '#青年',  model: 'containers', summary: '創業基地與貸款利息補貼。', facts: ['青創基地進駐', '創業貸款補貼'] },
    { id: 'culture',   title: '藝文場館',       tag: '#文化',  model: 'theater',    summary: '表演藝術中心與藝文補助。', facts: ['藝文活動補助', '場館營運'] },
    { id: 'metro',     title: '捷運路網延伸',   tag: '#交通',  model: 'station',    summary: '環狀線與東環段持續推進。', facts: ['東環段規劃', '站體周邊開發'] },
    { id: 'safety',    title: '通學與交通安全', tag: '#安全',  model: 'crossing',   summary: '校園周邊行人優先與路口改善。', facts: ['行穿線退縮與庇護島', '通學巷'] },
    { id: 'tourism',   title: '夜市與觀光',     tag: '#觀光',  model: 'nightmarket',summary: '夜市環境改善與國際行銷。', facts: ['夜市品質認證', '燈節與大型活動'] },
  ],
  issues: [
    { id: 'trees',      title: '護樹',           tag: '#護樹',   startsAt: '2026-10-01', intensity: 1.0, routes: ['west','north'], boss: null,       mix: { bird: 3, parrot: 1, mouse: 1 }, chants: ['護樹!!','樹呢??','不要砍樹','護樹護樹','移樹就是砍樹'], desc: '青鳥為主，從淡水河與關渡飛來。' },
    { id: 'roundabout', title: '拆圓環',         tag: '#拆圓環', startsAt: '2026-10-15', intensity: 1.1, routes: ['west','south'], boss: null,       mix: { frog: 3, bird: 1, caterpillar: 1 }, chants: ['拆圓環!!','圓環還我','公館塞爆','拆了幹嘛'], desc: '青蛙群從公館方向跳進來。' },
    { id: 'dome',       title: '大巨蛋',         tag: '#大巨蛋', startsAt: '2026-10-25', intensity: 1.2, routes: ['east','north'], boss: null,       mix: { parrot: 2, mouse: 3, frog: 1 }, chants: ['巨蛋漏水','票很難買','散場塞車','又在蛋'], desc: '鸚鵡與鍵盤鼠洗版。' },
    { id: 'nvidia',     title: '輝達總部',       tag: '#輝達',   startsAt: '2026-11-05', intensity: 1.3, routes: ['north','east'], boss: 'bigv',     mix: { chameleon: 2, bird: 2, mouse: 1 }, chants: ['T17 T18 呢','輝達要跑了','地上權卡關','北士科','髮夾彎'], desc: '變色龍帶隊，大 V 壓陣。' },
    { id: 'housing',    title: '社宅進度',       tag: '#社宅',   startsAt: '2026-11-15', intensity: 1.4, routes: ['west','south','east'], boss: 'frogking', mix: { caterpillar: 2, frog: 2, parrot: 1 }, chants: ['社宅呢','房租好貴','蓋太慢','租不起'], desc: '毛蟲慢慢爬，大青蛙王殿後。' },
    { id: 'final',      title: '大選前夜總攻擊', tag: '#總攻擊', startsAt: null, intensity: 2.2, routes: ['west','north','east','south'], boss: 'frogking', boss2: 'bigv', mix: { bird: 3, frog: 3, parrot: 2, caterpillar: 1, chameleon: 1, mouse: 4 }, chants: ['總攻擊!!','護樹!!','拆圓環!!','下台!!','最後一夜','全軍出動'], desc: '四面八方、所有動物、兩隻魔王。一樣守得下來。' },
  ],
  raidTags: ['#護樹','#拆圓環','#出來面對','#台北不順','#洗版中'],
  palette: { coral: 0x1f4e79, ink: 0x211d1d, cream: 0xfff8f4, yellow: 0xfcea8b, green: 0x8fb37a, stone: 0xd9cbb4, accent: 0xdb6a41 },
  ui: { logMax: 30, leaderboardMax: 10, heroesQueryLimit: 1000 },
};
window.PI_CONFIG = CONFIG;
