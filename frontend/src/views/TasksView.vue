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
  采集: { tone: 'blue', caption: '真实采集任务' },
  富化: { tone: 'red', caption: '暂未提供任务接口' },
  审核: { tone: 'teal', caption: '审核流程待联调' },
  问答: { tone: 'violet', caption: '会话记录由后端保存' },
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
      <p>查看各阶段的运行状态、时间与错误说明。</p>
    </div>
    <span class="panel-index">{{ store.tasks.length }} 条后台任务记录</span>
  </div>
  <PageState
    :loading="store.loading || !store.initialized"
    :error="store.error"
    :empty="store.tasks.length === 0"
    empty-text="暂无后台任务。"
    @retry="store.loadDemo"
  >
    <div class="tasks-layout">
      <OverviewMetrics :metrics="stageMetrics" label="各阶段任务数量" />
      <TaskRecordsPanel :tasks="store.tasks" :stages="stages" />
      <div class="task-note" role="note">
        <InfoFilled aria-hidden="true" />
        <p>
          <strong>运行说明</strong>
          采集任务由后端执行。当前页面只计入后端返回的采集任务，富化与人工审核任务尚未提供接口。问答记录可在会话中查看。
        </p>
      </div>
    </div>
  </PageState>
</template>
