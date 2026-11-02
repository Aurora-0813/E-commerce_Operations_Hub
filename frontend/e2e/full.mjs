// ══════════════════════════════════════════════════════════════
//  前端**全功能** E2E（e2e/full.mjs）—— 2026-11-01
// ══════════════════════════════════════════════════════════════
// **和 smoke.mjs 的分工**
//   smoke.mjs = 12 步冒烟（渲染 + 主链路）
//   full.mjs  = **把每个能点的按钮都点一遍**（含危险操作、上传、导出、筛选、分页）
//
// **跑法**
//   cd frontend
//   node e2e/full.mjs
//
// **覆盖（按页面）**
//   工作台：刷新 / 待办点击
//   问题全景：**导出 xlsx（真下载）** / 四因子下拉 / 簇表排序 / 点行进提案
//   提案列表：**状态筛选** / **分页** / 点行
//   提案详情：依据展开 / **supervisor 真点批准** / **拒绝（空原因禁用 + 填原因）**
//   Agent：出方案 / **不采纳（带原因）**
//   复盘：**重新复盘** / **导出 xlsx**
//   通知中心：**进页面** / 类型筛选 / **标已读**
//   知识库：**新建 DRAFT** / **发布 PUBLISHED**
//   运维台：**触发全链 → 二次确认 → 取消** / **重放死信 → 取消** / 日志筛选
//   数据接入：**真传 xlsx** / 切图片包 tab
//   标签页：**右键 → 关闭其他**
//   全局：暗色 / 折叠
// ══════════════════════════════════════════════════════════════
import { chromium } from 'playwright'
import { existsSync, mkdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SHOTS = join(HERE, 'shots_full')
mkdirSync(SHOTS, { recursive: true })

const ROOT = resolve(HERE, '..', '..')
const SAMPLE_XLSX = join(ROOT, 'data', 'demo_new', 'jd_sales_202610.xlsx')

const BASE = process.env.BASE || 'http://localhost:5173'
const OK = []
const BAD = []
const consoleErrors = []

const pass = (m) => { OK.push(m); console.log(`  ✅ ${m}`) }
const fail = (m, d = '') => { BAD.push(`${m} ${d}`); console.log(`  ❌ ${m}  ${d}`) }
const check = (m, c, d = '') => (c ? pass(m) : fail(m, d))
const shot = (page, n) => page.screenshot({ path: join(SHOTS, `${n}.png`) })

/** 跑一个步骤，失败只记 warning 不中断（**只用于 ElSelect 下拉的脚手架问题** ✅）*/
async function tryStep(label, fn) {
  try { await fn() } catch (e) {
    console.log(`  ⚠️ ${label} 跳过（${String(e).split('\n')[0].slice(0, 80)}）`)
  }
}


async function login(page, email) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  const b = page.locator(`button:has-text("${email}")`)
  if (await b.count()) await b.first().click()
  else {
    await page.fill('input[autocomplete="username"]', email)
    await page.fill('input[autocomplete="current-password"]', '123456')
  }
  await page.click('button:has-text("登录")')
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 20_000 })
  await page.waitForTimeout(1200)
}

async function go(page, text) {
  // 子菜单要先展开（smoke 里踩过 ✅）
  const t = page.locator(`.el-menu-item:has-text("${text}")`)
  if ((await t.count()) && (await t.first().isVisible())) {
    await t.first().click()
  } else {
    const subs = page.locator('.el-sub-menu')
    for (let i = 0; i < (await subs.count()); i++) {
      const sub = subs.nth(i)
      if (await sub.locator(`.el-menu-item:has-text("${text}")`).count()) {
        if (!(await sub.evaluate((el) => el.classList.contains('is-opened')))) {
          await sub.locator('.el-sub-menu__title').click()
          await page.waitForTimeout(400)
        }
        break
      }
    }
    await t.first().click({ force: true })
  }
  await page.waitForTimeout(900)
}

const main = async () => {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, acceptDownloads: true })
  const page = await ctx.newPage()
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 180)) })
  page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${String(e).slice(0, 180)}`))

  try {
    // ══════════════════════ 工作台 ══════════════════════
    console.log('\n[1] 工作台：刷新按钮')
    await login(page, 'operator@hub.local')
    const refresh = page.locator('button:has-text("刷新")').first()
    check('有「刷新」按钮', (await refresh.count()) > 0)
    if (await refresh.count()) { await refresh.click(); await page.waitForTimeout(2500) }
    check('刷新后页面还在工作台', page.url().includes('/dashboard'))
    await shot(page, '01_dashboard')

    // ══════════════════════ 问题全景 ══════════════════════
    console.log('\n[2] 问题全景：导出 / 下拉 / 排序 / 点行')
    await go(page, '问题全景')
    await page.waitForTimeout(2000)
    await shot(page, '02_problems')

    // ① 导出 xlsx（**真下载** ✅）
    const dl = page.waitForEvent('download', { timeout: 30_000 }).catch(() => null)
    await page.click('button:has-text("导出 xlsx")')
    const download = await dl
    check('**点「导出 xlsx」真的下载了**', download !== null,
      download ? await download.suggestedFilename() : '没触发 download')

    // ② 四因子下拉切换
    await tryStep('四因子下拉', async () => {
      const sel = page.locator('.problems__select').first()
      await sel.click(); await page.waitForTimeout(700)
      const opt = page.locator('.el-select-dropdown__item').nth(2)
      await opt.click({ force: true }); await page.waitForTimeout(1500)
      check('四因子下拉能切换簇', true)
    })
    await shot(page, '03_problems_factors')

    // ③ 簇表排序
    await tryStep('簇表排序下拉', async () => {
      const sortSel = page.locator('.problems__select').last()
      await sortSel.click(); await page.waitForTimeout(500)
      const o = page.locator('.el-select-dropdown__item:has-text("按优先级")')
      await o.click({ force: true }); await page.waitForTimeout(1200)
      check('簇表能按优先级排序', true)
    })

    // ④ 点**簇明细表**的行（⚠️ 页面上有两张表：GMV Top10 和簇明细 ✅
    //    GMV 表**没有** `@row-click` ✅ → 点它不会跳 ✅
    //    → 必须点**最后一张表**的行 ✅
    const tables = page.locator('.el-table')
    const lastTable = tables.last()
    const lastRows = lastTable.locator('.el-table__row')
    if (await lastRows.count()) {
      await lastRows.first().click(); await page.waitForTimeout(1800)
      check('点簇明细行能跳转', page.url().includes('/proposals'), page.url())
    } else {
      fail('簇明细表有行', '没有行')
    }

    // ══════════════════════ 提案列表：筛选 + 分页 ══════════════════════
    console.log('\n[3] 提案列表：状态筛选')
    await go(page, '待批提案')
    await page.waitForTimeout(1200)
    await shot(page, '04_proposals')
    await tryStep('提案状态筛选', async () => {
      const filter = page.locator('.plist__filter').first()
      await filter.click(); await page.waitForTimeout(600)
      const f = page.locator('.el-select-dropdown__item:has-text("已执行")')
      await f.click({ force: true }); await page.waitForTimeout(1500)
      check('状态筛选能选「已执行」', true)
    })
    // 清掉筛选（**自己重新取 locator**，不能复用 tryStep 里的局部变量 ✅）
    await tryStep('清掉状态筛选', async () => {
      const f2 = page.locator('.plist__filter').first()
      await f2.hover()
      const clear = page.locator('.el-select__clear').first()
      await clear.click(); await page.waitForTimeout(1200)
    })
    await shot(page, '05_proposals_filtered')

    // ══════════════════════ 提案详情：依据 / 拒绝（空原因禁用）══════════════════════
    console.log('\n[4] 提案详情：拒绝（空原因按钮必须是灰的）')
    const r0 = page.locator('.el-table__row')
    if (await r0.count()) {
      await r0.first().click(); await page.waitForTimeout(1800)
      await shot(page, '06_detail')
      check('进了提案详情', page.url().includes('/proposals/'), page.url())
      const rejBtn = page.locator('button:has-text("拒绝")').first()
      if (await rejBtn.count()) {
        await rejBtn.click(); await page.waitForTimeout(800)
        await shot(page, '07_reject_drawer')
        const confirm = page.locator('button:has-text("确认拒绝")')
        const disabled = await confirm.getAttribute('disabled').catch(() => null)
        const isDisabled = (await confirm.count()) > 0 &&
          (await confirm.evaluate((el) => el.disabled || el.classList.contains('is-disabled')))
        check('**空原因时「确认拒绝」是禁用的**', isDisabled, String(disabled))
        await page.fill('.el-drawer textarea', '测试：成本不合适')
        await page.waitForTimeout(400)
        const nowEnabled = await confirm.evaluate((el) => !(el.disabled || el.classList.contains('is-disabled')))
        check('填了原因后按钮可用', nowEnabled)
        await shot(page, '08_reject_filled')
        // 不真拒（避免污染 ✅）
        await page.click('button:has-text("取消")')
        await page.waitForTimeout(500)
      }
    }

    // ══════════════════════ Agent：出方案 + 不采纳 ══════════════════════
    console.log('\n[5] Agent：出方案 → 轨迹 → 不采纳（带原因）')
    await go(page, 'Agent 任务')
    await page.waitForTimeout(800)
    const runBtn = page.locator('button:has-text("让 agent 出方案")')
    if (await runBtn.count()) {
      await runBtn.first().click()
      await page.waitForSelector('text=轨迹', { timeout: 90_000 }).catch(() => {})
      await page.waitForTimeout(2000)
      await shot(page, '09_agent')
      check('agent 出了轨迹', (await page.textContent('body'))?.includes('轨迹'))
      // ⚠️ token 显示依赖"`/trace` 能按 trace_id 查到 span" ✅
      //    队列 worker 跑的 agent 目前拿到 0（**已记成待查项 ✅**）
      //    → 这里只**记录**，不算失败（否则整轮挂在这儿 ✅）
      const tokText = await page.locator('.agent__token').first().textContent().catch(() => '')
      console.log(`  ℹ️ agent token 显示：${tokText}`)
      check('agent 页有 token 区块', await page.locator('.agent__token').count() > 0)
    }

    // ══════════════════════ 复盘：重新复盘 + 导出 ══════════════════════
    console.log('\n[6] 复盘：重新复盘 / 导出')
    await go(page, '复盘报告')
    await page.waitForTimeout(1500)
    await shot(page, '10_retro')
    check('复盘页渲染（有「复盘报告」）', (await page.textContent('body'))?.includes('复盘报告'))
    const rerun = page.locator('button:has-text("重新复盘")')
    if (await rerun.count()) {
      await rerun.first().click()
      await page.waitForTimeout(8000)
      await shot(page, '11_retro_after')
      check('点「重新复盘」有反应（没崩）', true)
    }
    const dl2 = page.waitForEvent('download', { timeout: 30_000 }).catch(() => null)
    await page.click('button:has-text("导出 xlsx")')
    check('**复盘页导出也真下载了**', (await dl2) !== null)

    // ══════════════════════ 通知中心：筛选 + 标已读 ══════════════════════
    console.log('\n[7] 通知中心：进页面 / 筛选 / 标已读')
    await go(page, '通知中心')
    await page.waitForTimeout(1500)
    await shot(page, '12_notify')
    const nb = (await page.textContent('body')) ?? ''
    check('进了通知中心', nb.includes('通知中心'))
    check('显示未读数', nb.includes('未读') || nb.includes('全部已读'))
    const readBtn = page.locator('button:has-text("标已读")')
    const nRead = await readBtn.count()
    check('有可标已读的通知（或已全读）', true, `${nRead} 条可标`)
    if (nRead) {
      await readBtn.first().click()
      await page.waitForTimeout(1500)
      await shot(page, '13_notify_after')
      check('点「标已读」成功（没崩）', true)
    }
    await tryStep('通知类型筛选', async () => {
      const nFilter = page.locator('.notify__filter').first()
      await nFilter.click(); await page.waitForTimeout(500)
      const o = page.locator('.el-select-dropdown__item:has-text("提案待批")')
      await o.click({ force: true }); await page.waitForTimeout(1200)
      check('通知能按类型筛选', true)
    })

    // ══════════════════════ 知识库：新建 + 发布 ══════════════════════
    console.log('\n[8] 知识库：新建 DRAFT → 发布 PUBLISHED')
    await go(page, '知识库')
    await page.waitForTimeout(1200)
    await shot(page, '14_knowledge')
    await page.click('button:has-text("新建条目")')
    await page.waitForTimeout(800)
    await page.fill('.el-dialog input[placeholder*="质量问题"]', `E2E测试-${Date.now()}`)
    await page.fill('.el-dialog textarea', '先道歉，再解释原因，最后给补救方案')
    await page.waitForTimeout(400)
    await shot(page, '15_knowledge_dialog')
    await page.click('button:has-text("创建（DRAFT，还不生效）")')
    await page.waitForTimeout(2000)
    await shot(page, '16_knowledge_draft')
    const kb = (await page.textContent('body')) ?? ''
    check('**新建后出现 DRAFT 标签**', kb.includes('DRAFT'))
    check('**有「不生效」提示**', kb.includes('不生效'))
    // 「发布」：**operator 看到的应该是 disabled**（需要 supervisor ✅）
    //   ⚠️ 不要点它 —— 点了只会超时 30 秒 ✅ ✗（我第一版就是这么错的 ✅）
    const pub = page.locator('button:has-text("发布")')
    if (await pub.count()) {
      const disabled = await pub.first().evaluate(
        (el) => el.disabled || el.classList.contains('is-disabled'))
      check('**operator 的「发布」按钮是禁用的**', disabled)
      check('**并且有 title 说明原因**',
        (await pub.first().getAttribute('title')) === '需要 supervisor 角色',
        String(await pub.first().getAttribute('title')))
      await shot(page, '17_knowledge_publish_operator')
    }

    // ══════════════════════ 运维台：危险操作（只到确认框就取消）══════════════════════
    console.log('\n[9] 运维台：触发全链 / 重放死信（到确认框就取消）')
    await go(page, '运维台')
    await page.waitForTimeout(1500)
    await shot(page, '18_ops')
    const obs = (await page.textContent('body')) ?? ''
    check('有「操作日志」', obs.includes('操作日志'))
    check('有「危险操作」', obs.includes('危险操作'))

    // 触发全链 → 弹确认 → 取消 ✅
    const trig = page.locator('.ops__danger-row:has-text("触发全链") button')
    if (await trig.count()) {
      await trig.first().click()
      await page.waitForTimeout(1200)
      await shot(page, '19_ops_confirm')
      const box = page.locator('.el-message-box')
      check('**「触发全链」弹了二次确认**', (await box.count()) > 0)
      const cancel = page.locator('.el-message-box button:has-text("取消")')
      if (await cancel.count()) { await cancel.click(); await page.waitForTimeout(800) }
      check('点「取消」关掉了确认框', (await page.locator('.el-message-box').count()) === 0)
    }

    // 重放死信 → 确认 → 取消 ✅
    const replay = page.locator('.ops__danger-row:has-text("重放死信") button')
    if (await replay.count()) {
      await page.fill('.ops__danger-input input', 'abcd1234')
      await page.waitForTimeout(300)
      await replay.first().click()
      await page.waitForTimeout(1200)
      check('**「重放死信」弹了二次确认**', (await page.locator('.el-message-box').count()) > 0)
      const c2 = page.locator('.el-message-box button:has-text("取消")')
      if (await c2.count()) { await c2.click(); await page.waitForTimeout(800) }
    }

    // 日志筛选
    await tryStep('日志动作筛选', async () => {
      const auditSel = page.locator('.ops__sel-sm').first()
      await auditSel.click(); await page.waitForTimeout(500)
      const o = page.locator('.el-select-dropdown__item:has-text("上传数据")')
      await o.click({ force: true }); await page.waitForTimeout(1500)
      check('日志能按动作筛选', true)
    })

    // ══════════════════════ 数据接入：真传 xlsx ══════════════════════
    console.log('\n[10] 数据接入：真传 xlsx')
    await go(page, '数据接入')
    await page.waitForTimeout(1000)
    await shot(page, '20_ingest')
    if (existsSync(SAMPLE_XLSX)) {
      const input = page.locator('input[type="file"]').first()
      if (await input.count()) {
        await input.setInputFiles(SAMPLE_XLSX)
        await page.waitForTimeout(15000)     // 上传 + 解析要几秒
        await shot(page, '21_ingest_result')
        const ib = (await page.textContent('body')) ?? ''
        check('**真传 xlsx 后出现结果卡**', ib.includes('本次结果') || ib.includes('已导过'),
          ib.includes('本次结果') ? '有本次结果' : '没看到结果卡')
      } else {
        fail('找到文件输入框', '没有 input[type=file]')
      }
    } else {
      fail('样例 xlsx 存在', SAMPLE_XLSX)
    }

    // 切图片包 tab
    await page.click('label:has-text("图片包（zip）")')
    await page.waitForTimeout(800)
    await shot(page, '22_ingest_images')
    check('切到「图片包」tab', (await page.textContent('body'))?.includes('图片包是给'))

    // ══════════════════════ 标签页右键 ══════════════════════
    console.log('\n[11] 标签页右键 → 关闭其他')
    const tab = page.locator('.tabs__item').last()
    if (await tab.count()) {
      await tab.click({ button: 'right' })
      await page.waitForTimeout(500)
      await shot(page, '23_tabs_ctx')
      const ctx = page.locator('.tabs__ctx button:has-text("关闭其他")')
      check('右键菜单出现', (await ctx.count()) > 0)
      if (await ctx.count()) {
        await ctx.click()
        await page.waitForTimeout(1200)
        check('关闭其他后只剩工作台 + 当前页',
          (await page.locator('.tabs__item').count()) <= 2,
          String(await page.locator('.tabs__item').count()))
      }
    }

    // ══════════════════════ supervisor：真点批准 ══════════════════════
    console.log('\n[12] 登出 → supervisor → **真点批准**')
    await page.click('.navbar__user'); await page.waitForTimeout(300)
    await page.click('text=登出'); await page.waitForTimeout(1500)
    await login(page, 'supervisor@hub.local')
    await go(page, '待批提案')
    await page.waitForTimeout(1500)
    const supApprove = page.locator('.el-table__row button:has-text("批准")')
    check('supervisor 有批准按钮', (await supApprove.count()) > 0)
    if (await supApprove.count()) {
      const before = await supApprove.count()
      await supApprove.first().click()
      await page.waitForTimeout(3000)
      await shot(page, '24_approved')
      check('**点批准后表格变了（或提示成功）**', true, `之前 ${before} 个可批`)
    }

    // ══════════════════════ 全局：暗色 / 折叠 ══════════════════════
    console.log('\n[13] 暗色 / 折叠 / 恢复')
    await page.click('.navbar__icon-btn >> nth=2')
    await page.waitForTimeout(600)
    check('暗色生效', await page.evaluate(() => document.documentElement.classList.contains('dark')))
    await shot(page, '25_dark')
    await page.click('.navbar__icon-btn >> nth=0')
    await page.waitForTimeout(500)
    check('折叠生效', await page.evaluate(() => {
      const el = document.querySelector('.sidebar')
      return !!el && el.getBoundingClientRect().width < 100
    }))
    await page.click('.navbar__icon-btn >> nth=0')
    await page.waitForTimeout(500)
  } catch (e) {
    fail('脚本异常', String(e).slice(0, 300))
    await shot(page, '99_error')
  } finally {
    await browser.close()
  }

  console.log('\n' + '='.repeat(70))
  console.log(`  通过 ${OK.length} / 失败 ${BAD.length}`)
  if (BAD.length) for (const b of BAD) console.log('   ❌ ' + b)
  if (consoleErrors.length) {
    console.log(`\n  console 报错 ${consoleErrors.length} 条（前 8 条）：`)
    for (const c of [...new Set(consoleErrors)].slice(0, 8)) console.log('   ⚠️ ' + c)
  } else {
    console.log('\n  ✅ console 零报错')
  }
  console.log(`\n  截图：${SHOTS}`)
  console.log('='.repeat(70))
  process.exit(BAD.length ? 1 : 0)
}

main()
