<script setup>
import { computed, onMounted, ref } from 'vue'
import { ArrowRight, ChevronRight, ClipboardList, Database, FileText, History, Layers3, Menu, MessageSquare, Package, Plus, Search, X } from 'lucide-vue-next'
import { api } from './api'
import InquiryChat from './InquiryChat.vue'

const tabs = [{key:'desk', name:'询价工作台', icon:Layers3}, {key:'quotes', name:'供应商报价库', icon:Database}, {key:'drafts', name:'采购草稿', icon:FileText}]
const section = ref('desk'), menu = ref(false), input = ref(''), search = ref(''), error = ref('')
const busy = ref(false), ready = ref(false), active = ref(null), pending = ref(''), focusRequest = ref(0)
const store = ref({mode:'',today:'',quotes:[],history:[],drafts:[]})
const filtered = computed(() => store.value.quotes.filter(q => [q.code,q.supplier,q.product].some(v => v.toLowerCase().includes(search.value.toLowerCase()))))
const money = v => Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2})
const navTitle = computed(() => tabs.find(t => t.key === section.value).name)

// 初始化并刷新列表，业务结果单独保存在当前会话中。
async function refresh(){
  store.value = await api('workspace/')
  ready.value = true
}
async function initialize(){
  try { await refresh(); error.value='' } catch(e){error.value=e.message}
}
onMounted(initialize)
function navigate(key){ section.value=key; menu.value=false; error.value=''; search.value='' }
function newInquiry(){
  if (busy.value) return
  navigate('desk')
  active.value = null
  input.value = ''
  focusRequest.value++
}
async function openInquiry(id){
  if(busy.value) return
  busy.value=true
  try { const item=await api('inquiries/'+id+'/'); navigate('desk'); active.value=item; input.value='' } catch(e){error.value=e.message} finally{busy.value=false}
}
// 每次提交带当前轮次，防止旧页面覆盖其他标签页中的采购条件。
async function submit(){
  const message=input.value.trim()
  if(!message || busy.value || !ready.value) return
  busy.value = true
  pending.value = message
  input.value = ''
  error.value = ''
  try {
    const reply = await api('chat/', {message, inquiry_id:active.value?.id, revision:active.value?.revision})
    active.value = reply
    pending.value = ''
    await refresh()
  } catch (e) {
    if (pending.value && !input.value) input.value = message
    error.value = e.message
  } finally {
    pending.value = ''
    busy.value = false
  }
}
// 从对应的比价卡片确认报价，并带上最新会话版本校验并发更新。
async function confirmDraft(code){
  if(busy.value || !code) return
  busy.value=true; error.value=''
  try{
    active.value.draft=await api('inquiries/'+active.value.id+'/confirm/',{revision:active.value.revision,code})
    await refresh()
  }catch(e){error.value=e.message}finally{busy.value=false}
}
</script>

<template>
<div class="shell">
  <Transition name="fade"><button v-if="menu" class="veil" aria-label="关闭菜单" @click="menu=false"></button></Transition>
  <aside :class="['sidebar',{opened:menu}]">
    <div class="brand"><img src="/mark.svg" alt=""><div><strong>采价通</strong><small>QuoteLink</small></div></div>
    <button class="new" :disabled="busy" @click="newInquiry"><Plus :size="18"/> 新建询价</button>
    <p class="nav-label">工作空间</p>
    <button v-for="tab in tabs" :key="tab.key" :class="['nav',{selected:section===tab.key}]" :disabled="busy" @click="navigate(tab.key)"><component :is="tab.icon" :size="18"/>{{tab.name}}<span v-if="tab.key==='drafts' && store.drafts.length" class="nav-count">{{store.drafts.length}}</span></button>
    <div class="sidebar-history">
      <div class="history-title"><History :size="14"/>最近询价</div>
      <p v-if="!store.history.length" class="muted">暂无询价记录</p>
      <button v-for="item in store.history" :key="item.id" :class="['history-link',{chosen:section==='desk' && active?.id===item.id}]" :disabled="busy" :title="item.title" @click="openInquiry(item.id)">
        <MessageSquare :size="14"/><span>{{item.title}}</span>
      </button>
    </div>
    <div class="side-foot"><div class="avatar"><Package :size="18"/></div><div><b>采购工作空间</b><small>本机个人工作区</small></div></div>
  </aside>
  <div class="stage">
    <header><div class="crumb"><button class="mobile-menu plain" aria-label="打开菜单" @click="menu=true"><Menu :size="21"/></button><Layers3 :size="16"/><span>工作空间</span><ChevronRight :size="13"/><b>{{navTitle}}</b></div><span class="mode"><i :class="{offline:!ready}"></i>{{ready ? (store.mode==='model'?'模型工具模式':'本地演示模式'):'连接中'}}</span></header>
    <main>
      <Transition name="soft"><div v-if="error && section!=='desk'" role="alert" class="error">{{error}} <button v-if="!ready" @click="initialize">重试连接</button><button class="plain" aria-label="关闭提示" @click="error=''"><X :size="16"/></button></div></Transition>
      <Transition name="page" mode="out-in">
        <InquiryChat v-if="section==='desk'" key="desk" v-model="input" :inquiry="active" :pending="pending"
          :busy="busy" :ready="ready" :error="error" :focus-request="focusRequest" @submit="submit"
          @confirm="confirmDraft" @retry="initialize" @dismiss="error=''"/>
        <div v-else-if="section==='quotes'" key="quotes">
        <div class="page-heading"><div><h1>供应商报价库</h1><p>查看有效报价、整单运费、可供数量及预计到货日。</p></div><a href="/admin/procurement/quote/" target="_blank" rel="noopener" class="btn">管理报价 <ArrowRight :size="15"/></a></div>
        <section class="panel list-panel"><div class="panel-title"><Database :size="18"/><h2>有效报价</h2><small>{{filtered.length}} 条</small></div><label class="search"><Search :size="16"/><input v-model="search" placeholder="搜索品名、供应商或报价编号"></label><div class="table-scroll"><table><thead><tr><th>供应商 / 编号</th><th>品名</th><th>单价</th><th>整单运费</th><th>可供</th><th>预计到货</th></tr></thead><tbody><tr v-for="(q,i) in filtered" :key="q.code"><td><div class="supplier"><span :class="'vendor v'+i%3">{{q.supplier[0]}}</span><div><b>{{q.supplier}}</b><code>{{q.code}}</code></div></div></td><td>{{q.product}}</td><td>¥{{money(q.unit_price)}}</td><td>¥{{money(q.freight)}}</td><td>{{q.stock}} 台</td><td>{{q.arrival}}</td></tr></tbody></table></div><p v-if="!filtered.length" class="muted">没有符合条件的报价。</p></section>
        </div>
        <div v-else key="drafts">
        <div class="page-heading"><div><h1>采购草稿</h1><p>查看已确认的采购需求与保存时的报价。</p></div></div>
        <section class="panel list-panel"><div class="panel-title"><ClipboardList :size="19"/><h2>草稿列表</h2><small>{{store.drafts.length}} 份</small></div><div v-if="!store.drafts.length" class="empty-history large"><div><FileText/></div><b>暂无采购草稿</b><p>确认可行报价后，可在这里查看采购申请。</p><button class="btn" @click="newInquiry">新建询价 <Plus :size="15"/></button></div>
        <article v-for="d in store.drafts" :key="d.id" class="draft"><div><code>{{d.number}}</code><h3>{{d.needs.product}} × {{d.needs.quantity}}</h3><p>{{d.quote.supplier}} · {{d.quote.code}} · {{d.quote.arrival}} 到货</p><small>预算 ¥{{money(d.needs.budget)}} · 截止 {{d.needs.deadline}}</small></div><div><strong>¥{{money(d.quote.total)}}</strong><button class="link" @click="openInquiry(d.inquiry_id)">查看询价 <ArrowRight :size="13"/></button></div></article></section>
        </div>
      </Transition>
    </main>
  </div>
</div>
</template>
