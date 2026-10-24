/* 萬安政策島 TD — 所有可調參數都在這裡。其他檔案不得出現魔術數字。
   新增「議題攻擊」：在 ISSUES 加一筆（或由編輯者在大廳 UI / 共享資料庫 issues 集合新增）。
   新增「攻擊方動物」：在 ANIMALS 加一筆，model 指到 world.js 已有的外型（bird/frog/worm/lizard/mouse）。 */
window.PI_CONFIG = {
  site: {
    name: '萬安政策島',
    sub: 'TD・守住市政府',
    tag: '非官方・網友自製',
    credit: '互動島嶼的靈感來自 puma.taipei 的「HEART 政策島」。',
    disclaimer: '這是網友自製的塔防小遊戲，不是台北市政府或任何競選團隊的官方內容。塔的名稱取自公開報導過的北市政策，議題、數值、動物與路線純屬遊戲設定，與任何真實人物或團體無關。',
  },
  // 大選日（台北時間）。前一晚 00:00 起進入「總攻擊」模式；之後持續可重演。
  election: { date: '2026-11-28', tz: 'Asia/Taipei', finalIssueId: 'final' },
  round: {
    seconds: 180,          // 一場約 3 分鐘
    bossAt: 140,           // 魔王出場秒數
    surgeAt: 158,          // 這秒之後每 surgeEvery 秒全場「全民護城」，保證守得下來
    surgeEvery: 1.5, surgePct: 0.35, surgeBelowApproval: 40, // 滿意度低於此值也立刻啟動全民護城
    wipeAt: 176,           // 最後 4 秒清場
    startMoney: 300, startApproval: 100, approvalFloor: 1,
    sellRate: 0.7, maxLevel: 3, upgradeCostRate: 0.8, upgradeDmgRate: 1.45, upgradeRangeAdd: 0.15,
    killBountyScale: 1, baseSpawn: 30, intensityPerDay: 0, // baseSpawn × issue.intensity = 一場敵人數
    bursts: [ { from: 3, to: 45, share: 0.35 }, { from: 55, to: 98, share: 0.35 }, { from: 106, to: 138, share: 0.30 } ],
    windShift: { minGap: 30, maxGap: 55, duration: 5, speedMul: 1.5 },
    raid: { minGap: 28, maxGap: 48, size: 5, chance: 0.7 },
    pxPerSec: 0.85,        // 敵速 1.0 = 每秒走幾格
    projSpeed: 9,          // 子彈每秒幾格
    chantOn: 1.8, chantOff: 1.3,
    focusDmgMul: 3, focusCooldown: 1.4,   // 點擊敵人：英雄重擊
    ultCooldown: 45, ultPct: 0.6, ultSlow: 0.6, ultSlowTime: 3, // 「台北人站出來」大招
  },
  map: {
    cols: 10, rows: 10, goal: '5,5', heroTile: '6,6',
    districts: {
      '0,2':'北投','0,3':'北投','1,2':'北投','1,3':'北投','1,4':'北投','1,5':'北投',
      '2,1':'北投','2,2':'北投','2,3':'士林','2,4':'士林','2,5':'內湖','2,6':'內湖',
      '3,2':'士林','3,3':'士林','3,4':'士林','3,5':'內湖','3,6':'內湖','3,7':'內湖',
      '4,2':'大同','4,3':'中山','4,4':'中山','4,5':'松山','4,6':'內湖','4,7':'南港','4,8':'南港',
      '5,1':'萬華','5,2':'大同','5,3':'中山','5,4':'松山','5,5':'信義','5,6':'南港','5,7':'南港',
      '6,1':'萬華','6,2':'中正','6,3':'大安','6,4':'信義','6,5':'信義','6,6':'南港',
      '7,2':'中正','7,3':'大安','7,4':'大安','7,5':'文山',
      '8,3':'文山','8,4':'文山','8,5':'文山','9,4':'文山',
    },
    // 四條河道 = 四條攻擊路線。議題可指定只用其中幾條。
    paths: {
      west:  { name: '淡水河', cells: ['5,1','6,2','6,3','5,3','5,4','5,5'], from: { dr: 0, dc: -1 } },
      north: { name: '關渡',   cells: ['0,2','1,2','2,3','3,3','4,4','4,5','5,5'], from: { dr: -1, dc: 0 } },
      east:  { name: '基隆河', cells: ['4,8','4,7','5,6','5,5'], from: { dr: 0, dc: 1 } },
      south: { name: '景美溪', cells: ['9,4','8,4','7,4','6,4','6,5','5,5'], from: { dr: 1, dc: 0 } },
    },
    landmarks: { '7,3': { label: '公館（圓環已拆）', kind: 'ring' }, '4,5': { label: '松山機場', kind: 'airport' }, '2,2': { label: '北投溫泉', kind: 'spring' }, '3,6': { label: '內湖科技園區', kind: 'tech' } },
    districtTint: { '北投': 0xe8dcc0, '士林': 0xeadfc3, '內湖': 0xe3dcc0, '大同': 0xeedcbd, '中山': 0xeadbbd, '松山': 0xe6dabd, '南港': 0xdfd9bf, '萬華': 0xeedabb, '中正': 0xe9d9bd, '大安': 0xe6dbc0, '信義': 0xefdcb8, '文山': 0xdedbc0 },
  },
  // 攻擊方動物。chants 是該動物自己的口頭禪，議題口號另外給；兩者混著喊。
  animals: {
    bird:        { name: '青鳥',       model: 'bird',   color: 0x17a3b4, accent: 0x0d7281, hp: 40,   speed: 1.0,  bounty: 6,   size: 1.0, leak: 4,  fly: true,  chants: ['護樹!!','出來面對','怒'] },
    frog:        { name: '青蛙',       model: 'frog',   color: 0x4caf50, accent: 0x2e7d32, hp: 95,   speed: 0.65, bounty: 12,  size: 1.0, leak: 6,  hop: true,  chants: ['呱!!','呱呱呱','蛙~'] },
    parrot:      { name: '綠鸚鵡',     model: 'bird',   color: 0x6abf4b, accent: 0xd94a3a, hp: 60,   speed: 1.1,  bounty: 9,   size: 0.9, leak: 4,  fly: true,  repeat: true, chants: ['複製貼上','同上','+1'] },
    caterpillar: { name: '綠毛蟲',     model: 'worm',   color: 0x7cb342, accent: 0x558b2f, hp: 240,  speed: 0.38, bounty: 20,  size: 1.0, leak: 8,  chants: ['慢慢來','蠕動中','不急'] },
    chameleon:   { name: '變色龍',     model: 'lizard', color: 0x66bb6a, accent: 0xffb300, hp: 130,  speed: 0.9,  bounty: 16,  size: 1.0, leak: 6,  shift: true, hairpin: [0.45, 1.5], chants: ['髮夾彎','我沒說過','立場不變'] },
    mouse:       { name: '鍵盤鼠',     model: 'mouse',  color: 0x9e9e9e, accent: 0x616161, hp: 22,   speed: 1.6,  bounty: 3,   size: 0.7, leak: 1,  chants: ['已檢舉','笑死','洗起來','推'] },
    bigv:        { name: '側翼大 V',   model: 'bird',   color: 0x0e5f6b, accent: 0xf2c14e, hp: 950,  speed: 0.5,  bounty: 110, size: 2.0, leak: 25, fly: true,  boss: true, spawn: { type: 'mouse', every: 4 }, chants: ['下台!!','帶風向中','轉發一百次'] },
    frogking:    { name: '大青蛙王',   model: 'frog',   color: 0x2e7d32, accent: 0xf2c14e, hp: 1500, speed: 0.38, bounty: 160, size: 2.3, leak: 30, hop: true,  boss: true, spawn: { type: 'frog', every: 7 }, chants: ['呱!!!!','總攻擊','全軍出動'] },
  },
  // 防守方英雄職業（登入者選一個）。
  heroes: {
    chief:    { name: '里長伯',   icon: '🧓', color: 0xb68a2e, dmg: 16,  rate: 0.55, range: 3.0, style: 'ball',  desc: '人面廣，中距離穩定輸出。' },
    driver:   { name: '公車司機', icon: '🚌', color: 0x2f6f9f, dmg: 10,  rate: 0.3,  range: 2.8, style: 'bus',   desc: '班次密，連發。' },
    fireman:  { name: '消防員',   icon: '🚒', color: 0xc9452f, dmg: 18, rate: 0.7,  range: 2.6, style: 'water', slow: 0.5, slowTime: 1.6, desc: '水柱打到會減速。' },
    volunteer:{ name: '志工媽媽', icon: '🧺', color: 0xc97d97, dmg: 9,  rate: 0.6,  range: 2.6, style: 'ball',  regen: 0.4, support: 0.2, desc: '每秒回一點滿意度，鄰近塔加速 20%。' },
    engineer: { name: '工程師',   icon: '💻', color: 0x4a3f8f, dmg: 22, rate: 1.0,  range: 3.0, style: 'zap',   chain: 3, desc: '台北通連鎖電擊，一次打 3 隻。' },
  },
  towers: {
    tpass:   { name: 'TPASS 公車站',   icon: '🚌', cost: 30,  range: 1.7,  dmg: 8,  rate: 0.38, color: 0x2f6f9f, proj: 'bus',  desc: '1200 月票開進來。班次密、單發弱。' },
    housing: { name: '社會住宅',       icon: '🏢', cost: 60,  range: 1.9,  dmg: 5,  rate: 0.75, slow: 0.5, slowTime: 1.5, color: 0x6b8e5a, proj: 'key', desc: '看到房租打折，敵人減速一半。' },
    dome:    { name: '大巨蛋',         icon: '🏟️', cost: 120, range: 2.6,  dmg: 46, rate: 1.7,  splash: 0.9, color: 0xc96a3a, proj: 'ball', desc: '全壘打。慢，但範圍傷害。' },
    care:    { name: '托育家園',       icon: '🍼', cost: 80,  range: 1.35, support: 0.3, color: 0xc97d97, desc: '不攻擊。鄰近塔攻速 +30%，可疊加。' },
    senior:  { name: '敬老卡 1500 點', icon: '🧧', cost: 90,  range: 0,    income: 1.2, color: 0xb68a2e, desc: '不攻擊。每秒 +1.2 億預算。' },
    ai:      { name: '台北通闢謠站',   icon: '📡', cost: 150, range: 2.2,  dmg: 24, rate: 1.15, chain: 4, color: 0x4a3f8f, proj: 'zap', desc: '事實查核連鎖電擊，最多打 4 隻。' },
  },
  towerOrder: ['tpass','housing','dome','care','senior','ai'],
  // 議題攻擊排程。startsAt 之後成為「今日議題」（取最新一筆）；玩家也可手動選舊議題重演。
  // mix：動物權重；routes：使用哪些河道；boss：魔王（null = 無）；intensity：敵量倍率。
  issues: [
    { id: 'trees',      title: '護樹',       tag: '#護樹',     startsAt: '2026-10-01', intensity: 1.0, routes: ['west','north'], boss: null,       mix: { bird: 3, parrot: 1, mouse: 1 }, chants: ['護樹!!','樹呢??','不要砍樹','護樹護樹','移樹就是砍樹'], desc: '青鳥為主，沿淡水河與關渡飛來。' },
    { id: 'roundabout', title: '拆圓環',     tag: '#拆圓環',   startsAt: '2026-10-15', intensity: 1.1, routes: ['west','south'], boss: null,       mix: { frog: 3, bird: 1, caterpillar: 1 }, chants: ['拆圓環!!','圓環還我','公館塞爆','拆了幹嘛'], desc: '青蛙群從公館方向跳進來。' },
    { id: 'dome',       title: '大巨蛋',     tag: '#大巨蛋',   startsAt: '2026-10-25', intensity: 1.2, routes: ['east','north'], boss: null,       mix: { parrot: 2, mouse: 3, frog: 1 }, chants: ['巨蛋漏水','票很難買','散場塞車','又在蛋'], desc: '鸚鵡與鍵盤鼠洗版。' },
    { id: 'nvidia',     title: '輝達總部',   tag: '#輝達',     startsAt: '2026-11-05', intensity: 1.3, routes: ['north','east'], boss: 'bigv',     mix: { chameleon: 2, bird: 2, mouse: 1 }, chants: ['T17 T18 呢','輝達要跑了','地上權卡關','北士科','髮夾彎'], desc: '變色龍帶隊，大 V 壓陣。' },
    { id: 'housing',    title: '社宅進度',   tag: '#社宅',     startsAt: '2026-11-15', intensity: 1.4, routes: ['west','south','east'], boss: 'frogking', mix: { caterpillar: 2, frog: 2, parrot: 1 }, chants: ['社宅呢','房租好貴','蓋太慢','租不起'], desc: '毛蟲慢慢爬，大青蛙王殿後。' },
    { id: 'final',      title: '大選前夜總攻擊', tag: '#總攻擊', startsAt: null, intensity: 2.2, routes: ['west','north','east','south'], boss: 'frogking', boss2: 'bigv', mix: { bird: 3, frog: 3, parrot: 2, caterpillar: 1, chameleon: 1, mouse: 4 }, chants: ['總攻擊!!','護樹!!','拆圓環!!','下台!!','最後一夜','全軍出動'], desc: '四面八方、所有動物、兩隻魔王。一樣守得下來。' },
  ],
  raidTags: ['#護樹','#拆圓環','#出來面對','#台北不順','#洗版中'],
  ui: { logMax: 30, leaderboardMax: 10, heroesQueryLimit: 1000 },
};
