<script setup lang="ts">
import { onMounted, shallowRef, watch } from 'vue'
import type { IntelligenceItem } from '@/types'
import { errorMessage } from '@/api/http'
import { useRoute } from 'vue-router'
import PageState from '@/components/PageState.vue'
import EnrichmentOverview from '@/components/enrichment/EnrichmentOverview.vue'
import SourceEvidencePanel from '@/components/intelligence/SourceEvidencePanel.vue'
import { severityNames, useIntelligenceStore } from '@/stores/intelligence'

const route = useRoute()
const store = useIntelligenceStore()
const item = shallowRef<IntelligenceItem | null>(null)
const detailLoading = shallowRef(false), detailError = shallowRef('')
let detailRequest = 0
async function loadDetail() {
  const version = ++detailRequest
  detailLoading.value = true; detailError.value = ''; item.value = null
  try {
    const result = await store.loadDetail(String(route.params.id), typeof route.query.content_hash === 'string' ? route.query.content_hash : undefined)
    if (version === detailRequest) item.value = result
  } catch (error) {
    if (version === detailRequest) detailError.value = error instanceof Error && !('isAxiosError' in error) ? error.message : errorMessage(error)
  } finally { if (version === detailRequest) detailLoading.value = false }
}
onMounted(() => { void loadDetail() })
watch(() => [route.params.id, route.query.content_hash, store.isDemo], () => { void loadDetail() })
</script>

<template>
  <div class="back-line"><router-link to="/intelligence">← 返回情报中心</router-link></div>
  <PageState
    :loading="detailLoading"
    :error="detailError"
    :empty="!item"
    empty-text="未找到这条来源资料。"
    @retry="loadDetail"
  >
    <template v-if="item">
      <div class="detail-hero">
        <div>
          <span class="eyebrow">情报详情 / {{ item.id }}</span>
          <h1>{{ item.title }}</h1>
          <p v-if="item.documentType !== 'paper'">{{ item.summary }}</p>
          <div class="detail-meta">
            <span :class="['severity-badge', item.severity]">{{
              severityNames[item.severity]
            }}</span
            ><span>发布日期 {{ item.publishedAt || '来源未提供' }}</span
            ><span>来源 {{ item.source }}</span>
          </div>
        </div>
        <div class="detail-id">{{ item.id }}</div>
      </div>
      <div class="detail-grid">
        <div class="detail-main">
          <section class="panel">
            <div class="panel-title">
              <div>
                <h2>结构化信息</h2>
                <p v-if="item.structuredStatus === 'unavailable'">结构化字段获取失败；原文仍可查看，缺失字段暂未核对。</p>
                <p v-else-if="item.structuredStatus === 'historical'">当前为引用版本，未使用其他版本的结构化字段。</p>
                <p v-else>结构化信息来自当前版本来源及有原文证据的富化结果；未提供的字段如实标记。</p>
              </div>
            </div>
            <div class="field-grid">
              <div>
                <span>CVE 编号</span><strong>{{ item.cve || '暂无数据' }}</strong>
              </div>
              <div>
                <span>CVSS 评分</span><strong>{{ item.cvss ?? '暂无数据' }}</strong><small v-if="item.cvss === undefined && item.cvssVectors?.length">来源提供向量，未提供数值评分</small>
              </div>
              <div>
                <span>CWE 弱点</span><strong>{{ item.cwe || '暂无数据' }}</strong><small v-if="!item.cwe && item.sourceFields?.cwe === 'unknown'">来源未提供</small>
              </div>
              <div>
                <span>风险等级</span><strong>{{ severityNames[item.severity] }}</strong>
              </div>
            </div>
          </section>
          <EnrichmentOverview :item="item" @retry="loadDetail" />
          <SourceEvidencePanel v-if="item.documentType !== 'paper'" :item="item" />
        </div>
        <aside class="detail-aside">
          <section class="panel">
            <h2>情报标签</h2>
            <div class="tag-line">
              <span v-for="tag in item.tags" :key="tag" class="soft-tag">{{ tag }}</span
              ><span v-if="!item.tags.length" class="muted">暂无数据</span>
            </div>
          </section>
          <section class="panel callout-panel">
            <span class="eyebrow">来源追溯</span>
            <h2>追溯说明</h2><p>版本：<code style="overflow-wrap:anywhere">{{ item.contentHash }}</code></p><p>{{ item.isDemo ? "虚构测试资料" : "真实来源资料" }} · {{ item.completeness }}</p>
            <p>
              资料由后端保存，保留原文及版本。来源记录不等于独立人工核验。
            </p>
            <router-link :to="{ path: '/graph', query: item.sourceId ? { source_id: item.sourceId } : {} }">查看分类与来源关系</router-link>
          </section>
        </aside>
      </div>
    </template>
  </PageState>
</template>

<style scoped>
.field-grid small { display: block; color: #6f6f6f; font-size: 11px; margin-top: 6px; line-height: 1.7; }
</style>
