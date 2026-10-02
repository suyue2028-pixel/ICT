<script setup lang="ts">
import { computed } from 'vue'
import { InfoFilled } from '@element-plus/icons-vue'
import PageState from '@/components/PageState.vue'
import OverviewMetrics from '@/components/overview/OverviewMetrics.vue'
import TaskRecordsPanel from '@/components/tasks/TaskRecordsPanel.vue'
import type { OverviewMetric } from '@/composables/useOverviewData'
import { useIntelligenceStore } from '@/stores/intelligence'
import type { TaskStage } from '@/types'

const store = useIntelligenceStore()
const stages: TaskStage[] = ['采集', '富化', '审核', '问答']
const stageAppearance: Record<TaskStage, Pick<OverviewMetric, 'tone' | 'caption'>> = {
  采集: { tone: 'blue', caption: '本地采集样例' },
  富化: { tone: 'red', caption: '静态富化字段' },
  审核: { tone: 'teal', caption: '审核流程待联调' },
  问答: { tone: 'violet', caption: '问答服务未接入' },
}
const stageMetrics = computed<OverviewMetric[]>(() =>
  stages.map((stage) => ({
    label: `${stage}任务`,
    value: store.tasks.filter((task) => task.stage === stage).length,
    ...stageAppearance[stage],
  })),
)
</script>

<template>
  <div class="page-heading tasks-heading">
    <div>
      <span class="eyebrow">工作空间 / 任务记录</span>
      <h1>任务与运行状态</h1>
      <p>查看各阶段的演示状态、时间与错误说明。</p>
    </div>
    <span class="panel-index">{{ store.tasks.length }} 条静态演示记录</span>
  </div>
  <PageState
    :loading="store.loading || !store.initialized"
    :error="store.error"
    :empty="store.tasks.length === 0"
    empty-text="暂无演示任务。"
    @retry="store.loadDemo"
  >
    <div class="tasks-layout">
      <OverviewMetrics :metrics="stageMetrics" label="各阶段演示任务数量" />
      <TaskRecordsPanel :tasks="store.tasks" :stages="stages" />
      <div class="task-note" role="note">
        <InfoFilled aria-hidden="true" />
        <p>
          <strong>演示说明</strong>
          当前没有真实任务执行能力。采集、富化、审核与问答的运行状态需要联调后端接口后才能展示。
        </p>
      </div>
    </div>
  </PageState>
</template>
