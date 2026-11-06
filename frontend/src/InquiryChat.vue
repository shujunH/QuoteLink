<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ArrowRight, ArrowUp, Boxes, CalendarDays, Check, ChevronDown, CircleCheck, FileCheck2, ListChecks, LoaderCircle, Monitor, Package, Sparkles, Wallet } from 'lucide-vue-next'
import HeroArt from './HeroArt.vue'

const props = defineProps({
  inquiry: Object, pending: String, busy: Boolean, ready: Boolean,
  error: String, focusRequest: Number
})
const emit = defineEmits(['submit', 'confirm', 'retry', 'dismiss'])
const input = defineModel({ type: String, default: '' })
const composer = ref(null)
const log = ref(null)
const expanded = ref([])
const showDraft = ref(false)
const turns = computed(() => props.inquiry?.turns ?? [])
const needs = computed(() => props.inquiry?.needs ?? {})
const money = value => Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const fields = computed(() => [
  { label: '品名', icon: Package, value: needs.value.product },
  { label: '数量', icon: Boxes, value: needs.value.quantity && needs.value.quantity + ' 台' },
  { label: '预算', icon: Wallet, value: needs.value.budget && '¥' + money(needs.value.budget) },
  { label: '到货', icon: CalendarDays, value: needs.value.deadline }
])
const starters = [
  { title: '20 台显示器采购', icon: Monitor, text: '下周三前要到货20台显示器，总预算2.4万元，含运费。请比较可行报价。' },
  { title: '什么是运费成本？', icon: Wallet, text: '什么是运费成本？' },
  { title: '采购品类有哪些？', icon: Boxes, text: '采购品类有哪些？' }
]

// 只滚动聊天记录，定位到本轮回复开头，让输入框始终可用。
async function revealLatest(animate = false) {
  await nextTick()
  const container = log.value
  const items = container?.querySelectorAll('.turn')
  const latest = items?.[items.length - 1]
  if (!latest) {
    if (container) container.scrollTop = 0
    return
  }
  const offset = latest.getBoundingClientRect().top - container.getBoundingClientRect().top
  const smooth = animate && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  container.scrollTo({ top: container.scrollTop + offset - 14, behavior: smooth ? 'smooth' : 'instant' })
}
onMounted(() => revealLatest())
watch(() => [props.inquiry?.id, props.inquiry?.revision, props.pending], () => revealLatest(true))
watch(() => props.inquiry?.id, () => { expanded.value = []; showDraft.value = false })
watch(() => props.inquiry?.revision, () => { showDraft.value = false })
watch(() => props.focusRequest, () => nextTick(() => composer.value?.focus({preventScroll: true})))
function fill(text) {
  input.value = text
  composer.value?.focus({preventScroll: true})
}
function toggleTrace(index) {
  expanded.value = expanded.value.includes(index) ? expanded.value.filter(i => i !== index) : [...expanded.value, index]
}
</script>

<template>
<section class="chat-window" aria-label="采购询价对话">
  <div class="chat-heading">
    <div><h1>采购问答与询价</h1><p>了解采购知识、比较供应商报价并保存采购草稿。</p></div>
    <HeroArt/>
  </div>
  <div v-if="Object.keys(needs).length" class="need-strip" aria-label="当前采购条件">
    <span v-for="field in fields" :key="field.label" :title="field.label"><component :is="field.icon" :size="14"/>
      {{field.label}} <b>{{field.value || '待填写'}}</b>
    </span>
  </div>
  <div ref="log" class="chat-log" tabindex="0" role="region" aria-label="询价记录">
    <div v-if="!turns.length && !pending" class="chat-welcome">
      <span class="assistant-mark"><Sparkles :size="22"/></span>
      <h2>有什么采购问题？</h2><p>可以先问采购常识，也可以告诉我品名、数量、预算和到货日来比价。</p>
      <div class="starter-list"><button v-for="item in starters" :key="item.title" @click="fill(item.text)">
        <component :is="item.icon" :size="16"/>{{item.title}}<ArrowRight :size="14"/>
      </button></div>
    </div>
    <div v-else :key="inquiry?.id || 'new'" class="turn-list">
      <article v-for="(turn, index) in turns" :key="index" class="turn">
        <div class="user-bubble">{{turn.user}}</div>
        <div class="speaker"><span><Sparkles :size="14"/></span><b>采价通</b><small>第 {{index + 1}} 轮</small></div>
        <p class="assistant-text">{{turn.message}}</p>
        <section v-if="turn.rows?.length" class="quote-result" :aria-label="'第 '+(index+1)+' 轮询价结果'">
          <div class="result-heading"><ListChecks :size="17"/><h3>询价结果</h3><small>{{turn.rows.length}} 份报价 · 含运费</small></div>
          <div v-for="row in turn.rows" :key="row.code" :class="['offer', {recommended: row.code === turn.recommended}]">
            <div class="offer-top"><b>{{row.supplier}}</b><code>{{row.code}}</code><span v-if="row.code === turn.recommended" class="best-label"><Check :size="12"/>推荐</span><strong>¥{{money(row.total)}}</strong></div>
            <p>¥{{money(row.unit_price)}} × {{turn.needs.quantity}} + 运费 ¥{{money(row.freight)}} · {{row.arrival}} 到货</p>
            <small :class="{pass:row.feasible}">{{row.feasible ? '满足全部采购条件' : row.reasons.join('；')}}</small>
          </div>
          <div v-if="index + 1 === inquiry.comparison_revision && turn.recommended" class="result-action">
            <Transition name="soft" mode="out-in">
              <button v-if="!inquiry.draft" class="btn" :disabled="busy" @click="emit('confirm', turn.recommended)"><FileCheck2 :size="15"/>确认并保存草稿</button>
              <div v-else class="saved"><CircleCheck :size="16"/>已保存 {{inquiry.draft.number}}
                <button class="link" :aria-expanded="showDraft" @click="showDraft=!showDraft">{{showDraft ? '收起草稿' : '查看草稿'}}</button>
              </div>
            </Transition>
            <Transition name="soft"><div v-if="showDraft && inquiry.draft" class="inline-draft">
              <b>{{inquiry.draft.needs.product}} × {{inquiry.draft.needs.quantity}}</b>
              <p>{{inquiry.draft.quote.supplier}} · {{inquiry.draft.quote.code}} · 含运费 ¥{{money(inquiry.draft.quote.total)}}</p>
              <p>采购预算 ¥{{money(inquiry.draft.needs.budget)}} · 最晚 {{inquiry.draft.needs.deadline}} 到货</p>
            </div></Transition>
          </div>
        </section>
        <template v-if="turn.trace?.length">
          <button class="trace-toggle" :aria-expanded="expanded.includes(index)" @click="toggleTrace(index)">
            工具调用记录（{{turn.trace.length}}）<ChevronDown :size="14" :class="{flipped:expanded.includes(index)}"/>
          </button>
          <Transition name="soft"><div v-if="expanded.includes(index)" class="trace">
            <div v-for="(call,i) in turn.trace" :key="i"><code>{{call.tool}}</code><pre>{{JSON.stringify({参数:call.args,返回:call.result},null,2)}}</pre></div>
          </div></Transition>
        </template>
      </article>
      <article v-if="pending" class="turn pending"><div class="user-bubble">{{pending}}</div>
        <div class="speaker" role="status"><span><LoaderCircle class="spin" :size="14"/></span>正在回复…</div>
      </article>
    </div>
  </div>
  <div class="chat-footer">
    <Transition name="soft"><div v-if="error" role="alert" class="error">{{error}}
      <button v-if="!ready" @click="emit('retry')">重试连接</button><button class="plain" aria-label="关闭提示" @click="emit('dismiss')">×</button>
    </div></Transition>
    <div v-if="inquiry?.comparison_revision" class="examples">
      <button v-if="turns[inquiry.comparison_revision - 1]?.rows?.length" :disabled="busy" @click="fill('解释一下刚才的比价结果。')">解释报价 <ArrowRight :size="12"/></button>
      <button :disabled="busy" @click="fill('预算改成2.3万元，其他条件不变。')">调整预算 <ArrowRight :size="12"/></button><button :disabled="busy" @click="fill('重新比价')">重新比价 <ArrowRight :size="12"/></button>
    </div>
    <form class="input-wrap" @submit.prevent="emit('submit')">
      <textarea ref="composer" v-model="input" rows="2" maxlength="1200" aria-label="采购问题或询价需求" placeholder="输入采购问题或询价需求…" @keydown.ctrl.enter.prevent="emit('submit')" @keydown.meta.enter.prevent="emit('submit')"></textarea>
      <div class="send-row"><small>{{input.length}} / 1200</small><button class="btn" :disabled="busy || !input.trim() || !ready"><LoaderCircle v-if="pending" class="spin" :size="15"/><ArrowUp v-else :size="16"/>{{pending?'处理中':'发送'}}</button></div>
    </form>
  </div>
</section>
</template>

<style src="./chat.css"></style>
