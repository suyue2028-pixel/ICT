<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import PageState from '@/components/PageState.vue'
import { severityNames, useIntelligenceStore } from '@/stores/intelligence'

const route = useRoute()
const store = useIntelligenceStore()
const item = computed(() => store.findById(String(route.params.id)))
</script>

<template>
  <div class="back-line"><router-link to="/intelligence">← 返回情报中心</router-link></div>
  <PageState
    :loading="store.loading || !store.initialized"
    :error="store.error"
    :empty="!item"
    empty-text="未找到这条演示情报。"
    @retry="store.loadDemo"
  >
    <template v-if="item">
      <div class="detail-hero">
        <div>
          <span class="eyebrow">情报详情 / {{ item.id }}</span>
          <h1>{{ item.title }}</h1>
          <p>{{ item.summary }}</p>
          <div class="detail-meta">
            <span :class="['severity-badge', item.severity]">{{
              severityNames[item.severity]
            }}</span
            ><span>发布日期 {{ item.publishedAt }}</span
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
                <p>缺失字段如实显示“暂无数据”</p>
              </div>
            </div>
            <div class="field-grid">
              <div>
                <span>CVE 编号</span><strong>{{ item.cve || '暂无数据' }}</strong>
              </div>
              <div>
                <span>CVSS 评分</span><strong>{{ item.cvss ?? '暂无数据' }}</strong>
              </div>
              <div>
                <span>CWE 弱点</span><strong>{{ item.cwe || '暂无数据' }}</strong>
              </div>
              <div>
                <span>风险等级</span><strong>{{ severityNames[item.severity] }}</strong>
              </div>
            </div>
          </section>
          <section class="panel">
            <div class="panel-title">
              <div>
                <h2>影响与建议</h2>
                <p>仅供演示，不构成实际安全评估</p>
              </div>
            </div>
            <div class="detail-section">
              <h3>受影响产品</h3>
              <div v-if="item.products.length" class="tag-line">
                <span v-for="product in item.products" :key="product" class="soft-tag">{{
                  product
                }}</span>
              </div>
              <p v-else class="muted">暂无数据</p>
            </div>
            <div class="detail-section">
              <h3>修复建议</h3>
              <p>{{ item.recommendation || '暂无数据' }}</p>
            </div>
          </section>
          <section class="panel">
            <div class="panel-title">
              <div>
                <h2>来源与证据</h2>
                <p>演示证据，不可作为真实漏洞证明</p>
              </div>
            </div>
            <div class="source-line">
              <span>来源名称</span><strong>{{ item.source || '暂无数据' }}</strong>
            </div>
            <div v-if="item.evidence.length" class="evidence-list">
              <div v-for="(evidence, index) in item.evidence" :key="index" class="evidence-card">
                <span class="evidence-num">0{{ index + 1 }}</span>
                <div>
                  <strong>{{ evidence.title }}</strong>
                  <p>{{ evidence.excerpt || '暂无数据' }}</p>
                  <a
                    v-if="evidence.url"
                    :href="evidence.url"
                    target="_blank"
                    rel="noopener noreferrer"
                    >查看原始来源 ↗</a
                  ><span v-else class="muted">原始链接：暂无数据</span>
                </div>
              </div>
            </div>
            <div v-else class="muted">暂无数据</div>
          </section>
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
            <h2>追溯说明</h2>
            <p>
              当前编号、字段与证据均由本地演示数据生成。待真实接口交付后，再展示实际采集来源与处理过程。
            </p>
            <router-link to="/graph">查看关联图谱 →</router-link>
          </section>
        </aside>
      </div>
    </template>
  </PageState>
</template>
