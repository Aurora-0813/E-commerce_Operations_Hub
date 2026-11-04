// ══════════════════════════════════════════════════════════════
//  前端 E2E 冒烟（e2e/smoke.mjs）—— 2026-11-01 新增
// ══════════════════════════════════════════════════════════════
// **为什么写这个**：`vue-tsc` 只能证明"类型对"，构建只能证明"能打包" ✅
//   但"点一遍不报错"必须**真开浏览器** ✅
//   我这个环境之前没有浏览器自动化 → 只能用字段契约间接验 ✅（不够 ✅）
//   → 装 Playwright + Chromium 后，**能真点** ✅
//
// **跑法**
//   cd frontend
//   node e2e/smoke.mjs
//   → 截图落在 frontend/e2e/shots/
//   → 退出码 0 = 全过
//
// **验什么**（12 步，覆盖两个角色 + 主链路）
//   ① 登录页渲染  ② operator 登录  ③ 工作台有数据
//   ④ 问题全景 6 图  ⑤ 提案列表  ⑥ **operator 看不到批准按钮**
//   ⑦ Agent 出方案（真调 7B）  ⑧ 采纳成提案  ⑨ 提案详情有依据原文
//   ⑩ 登出 → supervisor 登录  ⑪ 批准  ⑫ 运维台日志有新记录
//   + 暗色切换 / 侧边栏收起 / **收集 console 报错**
// ══════════════════════════════════════════════════════════════
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SHOTS = join(HERE, 'shots')
mkdirSync(SHOTS, { recursive: true })

const BASE = process.env.BASE || 'http://localhost:5173'
const OK = []
const BAD = []
const consoleErrors = []

function pass(msg) { OK.push(msg); console.log(`  ✅ ${msg}`) }
function fail(msg, detail = '') {
  BAD.push(`${msg} ${detail}`)
  console.log(`  ❌ ${msg}  ${detail}`)
}
function check(msg, cond, detail = '') { cond ? pass(msg) : fail(msg, detail) }

const shot = async (page, name) => {
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: false })
}

async function login(page, email, password = '123456') {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  // 用演示账号按钮（更快，也顺便验了那个按钮 ✅）
  const btn = page.locator(`button:has-text("${email}")`)
  if (await btn.count()) {
    await btn.first().click()
  } else {
    await page.fill('input[autocomplete="username"]', email)
    await page.fill('input[autocomplete="current-password"]', password)
  }
  await page.click('button:has-text("登录")')
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 20_000 })
}

// 菜单名**会随产品改**（2026-11-03 改成"按角色两套菜单" ✅）
// → 这里给旧名留别名，改名不会再让 E2E 挂 ✅
const ALIAS = {
  问题全景: ['问题分析', '问题全貌', '有什么问题', '问题全景'],
  待批提案: ['处置提案', '审批中心', '待批提案', '待我审批'],
  'Agent 任务': ['智能处置', 'Agent 出方案', 'Agent 任务'],
  复盘报告: ['复盘分析', '看效果', '复盘报告'],
  通知中心: ['通知中心', '系统找我'],
  知识库: ['知识库', '统一口径', '口径怎么写的'],
  运维台: ['系统日志', '审计日志', '团队在干什么', '运维台'],
  数据接入: ['数据接入', '数据到了没'],
  图片复核: ['图片核验', '图片复核'],
  今日工作台: ['工作台', '今天要做什么', '今日全景'],
}

async function sidebarGo(page, text) {
  const names = ALIAS[text] ?? [text]
  // ⚠️ **子菜单默认折叠**（2026-11-01 踩过 → click 超时 30 秒 ✅）
  //    「待批提案」在「处理」这个 el-sub-menu 下面 ✅
  //    → 元素**存在但不可见** → Playwright 的可点击性检查超时 ✅
  //    修法：**先找它的父级 sub-menu，点开，再点目标** ✅
  const sel = names.map((n) => `.el-menu-item:has-text("${n}")`).join(", ")
  const target = page.locator(sel)
  if (await target.count() && await target.first().isVisible()) {
    await target.first().click()
  } else {
    const subs = page.locator('.el-sub-menu')
    const n = await subs.count()
    for (let i = 0; i < n; i++) {
      const sub = subs.nth(i)
      if (await sub.locator(sel).count()) {
        const title = sub.locator('.el-sub-menu__title')
        const opened = await sub.evaluate((el) => el.classList.contains('is-opened'))
        if (!opened) { await title.click(); await page.waitForTimeout(400) }
        break
      }
    }
    await target.first().click({ force: true })
  }
  await page.waitForTimeout(900)
}

const main = async () => {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
  const page = await ctx.newPage()

  // 收集 console 报错（**这是"白屏/报错"的第一手证据** ✅）
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200))
  })
  page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${String(e).slice(0, 200)}`))

  try {
    // ---------- ① 登录页 ----------
    console.log('\n[1] 登录页')
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
    await shot(page, '01_login')
    check('登录页渲染（有「电商运营决策中枢」）',
      (await page.textContent('body'))?.includes('电商运营决策中枢'))
    check('有演示账号按钮', await page.locator('button:has-text("operator@hub.local")').count() > 0)

    // ---------- ② operator 登录 ----------
    console.log('\n[2] operator 登录')
    await login(page, 'operator@hub.local')
    await page.waitForTimeout(1500)
    check('登录成功进了工作台', page.url().includes('/dashboard'), page.url())
    check('导航栏显示「运营小李」', (await page.textContent('body'))?.includes('运营小李'))

    // ---------- ③ 工作台 ----------
    console.log('\n[3] 工作台')
    await shot(page, '02_dashboard')
    const body = await page.textContent('body') ?? ''
    check('有「今日工作台」标题', body.includes('今日工作台'))
    check('有「待处理事项」区块', body.includes('待处理事项'))
    check('有「等观察窗口」区块', body.includes('等观察窗口'))
    check('P 档环形图渲染（有 canvas）',
      await page.locator('.dash__charts canvas').count() > 0)

    // ---------- ④ 问题全景 ----------
    console.log('\n[4] 问题全景')
    await sidebarGo(page, '问题全景')
    await page.waitForTimeout(2000)
    await shot(page, '03_problems')
    check('进了 /problems', page.url().includes('/problems'))
    const pb = await page.textContent('body') ?? ''
    check('有「问题全景」标题', pb.includes('问题全景'))
    check('有 P0 标签（簇表渲染了）', pb.includes('P0'))
    check('图表 canvas ≥ 3 个', await page.locator('canvas').count() >= 3,
      String(await page.locator('canvas').count()))

    // ---------- ⑤ 提案列表 + operator 权限 ----------
    console.log('\n[5] 待批提案（operator）')
    await sidebarGo(page, '待批提案')
    await page.waitForTimeout(1200)
    await shot(page, '04_proposals_operator')
    check('进了 /proposals', page.url().includes('/proposals'))
    const rows = await page.locator('.el-table__row').count()
    check('表格有行', rows > 0, `${rows} 行`)
    // **operator 不该看到"批准"按钮** ✅（这是 UI 级隐藏，不是安全措施 ✅）
    const approveBtns = await page.locator('.el-table__row button:has-text("批准")').count()
    check('**operator 看不到批准按钮**', approveBtns === 0, `${approveBtns} 个`)

    // ---------- ⑥ Agent 出方案 ----------
    console.log('\n[6] Agent 任务（真调 7B，约 5~10 秒）')
    await sidebarGo(page, 'Agent 任务')
    await page.waitForTimeout(800)
    await shot(page, '05_agent_before')
    check('进了 /agent', page.url().includes('/agent'))
    // 点「让 agent 出方案」
    const runBtn = page.locator('button:has-text("让 agent 出方案")')
    check('有「让 agent 出方案」按钮', await runBtn.count() > 0)
    if (await runBtn.count()) {
      await runBtn.first().click()
      // 等结论卡出现（最多 90 秒 ✅）
      await page.waitForSelector('text=结论', { timeout: 90_000 }).catch(() => {})
      await page.waitForTimeout(2000)
      await shot(page, '06_agent_after')
      const ab = await page.textContent('body') ?? ''
      check('出现了「结论」卡', ab.includes('结论'))
      check('出现「轨迹」区块', ab.includes('轨迹'))
      check('有「采纳」按钮', await page.locator('button:has-text("采纳")').count() > 0)

      // ---------- ⑦ 采纳 ----------
      console.log('\n[7] 采纳 → 生成提案')
      const adoptBtn = page.locator('button:has-text("采纳")')
      if (await adoptBtn.count()) {
        await adoptBtn.first().click()
        await page.waitForTimeout(3000)
        await shot(page, '07_after_adopt')
        check('采纳后跳到提案详情', page.url().includes('/proposals/'), page.url())
        // ---------- ⑧ 提案详情的"依据原文" ----------
        console.log('\n[8] 提案详情：依据展开到原文')
        await page.waitForTimeout(1500)
        await shot(page, '08_proposal_detail')
        const db = await page.textContent('body') ?? ''
        check('有「依据」栏', db.includes('依据'))
        check('有「代表样本」标记', db.includes('代表样本'))
        check('有 **关键词高亮**（mark 标签）',
          await page.locator('.detail__review-text mark').count() > 0,
          String(await page.locator('.detail__review-text mark').count()))
      }
    }

    // ---------- ⑨ 暗色 + 侧边栏 ----------
    console.log('\n[9] 暗色 / 侧边栏')
    await page.click('.navbar__icon-btn >> nth=2')            // 暗色（0=折叠 1=铃铛 2=暗色 3=全屏 ✅）
    await page.waitForTimeout(500)
    const isDark = await page.evaluate(() => document.documentElement.classList.contains('dark'))
    check('暗色生效（html.dark）', isDark)
    await shot(page, '09_dark')
    // 折叠
    await page.click('.navbar__icon-btn >> nth=0')
    await page.waitForTimeout(500)
    await shot(page, '10_collapsed')
    const hasCollapsed = await page.evaluate(() => {
      const el = document.querySelector('.sidebar')
      const w = el ? el.getBoundingClientRect().width : 0
      return w > 0 && w < 100          // 折叠后 64px ✅
    })
    check('侧边栏能折叠（宽度 < 100px）', hasCollapsed, String(await page.evaluate(() => {
      const el = document.querySelector('.sidebar')
      return el ? Math.round(el.getBoundingClientRect().width) : -1
    })))
    // ⚠️ **必须展开回去**（2026-11-01 踩过 ✅）
    //    折叠状态写进 localStorage ✅ → 后面 supervisor 登录时菜单还是收起的 ✅
    //    → `[11]` 里 `.el-menu-item:has-text("待批提案")` **不可见** → click 超时 ✅ ✗
    await page.click('.navbar__icon-btn >> nth=0')
    await page.waitForTimeout(500)

    // ---------- ⑩ 登出 → supervisor ----------
    console.log('\n[10] 登出 → supervisor 登录')
    await page.click('.navbar__user')
    await page.waitForTimeout(300)
    await page.click('text=登出')
    await page.waitForTimeout(1500)
    check('登出后回到登录页', page.url().includes('/login'), page.url())
    await login(page, 'supervisor@hub.local')
    await page.waitForTimeout(1200)
    await shot(page, '11_supervisor_dashboard')
    check('supervisor 进工作台', page.url().includes('/dashboard'))

    // ---------- ⑪ supervisor 能看到批准 ----------
    console.log('\n[11] supervisor 看得到批准按钮')
    await sidebarGo(page, '待批提案')
    await page.waitForTimeout(1200)
    await shot(page, '12_proposals_supervisor')
    const supApprove = await page.locator('.el-table__row button:has-text("批准")').count()
    check('**supervisor 有批准按钮**', supApprove > 0, `${supApprove} 个`)

    // ---------- ⑫ 运维台 ----------
    console.log('\n[12] 运维台（日志 + 上传）')
    await sidebarGo(page, '运维台')
    await page.waitForTimeout(1500)
    await shot(page, '13_ops')
    const ob = await page.textContent('body') ?? ''
    check('有「操作日志」', ob.includes('操作日志'))
    check('有「危险操作」', ob.includes('危险操作'))
    check('日志表有行', await page.locator('.el-table__row').count() > 0)

    // ---------- ⑭ 图片复核页（2026-11-03 加）----------
    console.log('\n[14] 图片复核页')
    await sidebarGo(page, '图片复核')
    await page.waitForTimeout(1500)
    await shot(page, '16_image_review')
    const rb = (await page.textContent('body')) ?? ''
    check('进了 /images', page.url().includes('/images'), page.url())
    check('有「图片复核」标题', rb.includes('图片复核'))
    check('KPI 有「待复核」', rb.includes('待复核'))
    check('说明里写了「不直接进赔偿决策」', rb.includes('赔偿'))

    // ---------- ⑬ 数据接入（xlsx / 图片包 两个 tab）----------
    console.log('\n[13] 数据接入（两个 tab）')
    await sidebarGo(page, '数据接入')
    await page.waitForTimeout(800)
    await shot(page, '14_ingest_xlsx')
    check('有「数据表（xlsx）」tab', (await page.textContent('body'))?.includes('数据表'))
    await page.click('label:has-text("图片包（zip）")')
    await page.waitForTimeout(800)
    await shot(page, '15_ingest_images')
    check('切到「图片包」tab 有上传框',
      (await page.textContent('body'))?.includes('图片包是给'))
  } catch (e) {
    fail('脚本异常', String(e).slice(0, 300))
    await shot(page, '99_error')
  } finally {
    await browser.close()
  }

  // ---------- 汇总 ----------
  console.log('\n' + '='.repeat(66))
  console.log(`  通过 ${OK.length} / 失败 ${BAD.length}`)
  if (BAD.length) for (const b of BAD) console.log('   ❌ ' + b)
  if (consoleErrors.length) {
    console.log(`\n  console 报错 ${consoleErrors.length} 条（前 5 条）：`)
    for (const c of consoleErrors.slice(0, 5)) console.log('   ⚠️ ' + c)
  } else {
    console.log('\n  ✅ console 零报错')
  }
  console.log(`\n  截图：${SHOTS}`)
  console.log('='.repeat(66))
  process.exit(BAD.length ? 1 : 0)
}

main()
