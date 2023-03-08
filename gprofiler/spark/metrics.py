#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
# (C) Datadog, Inc. 2018-present. All rights reserved.
# Licensed under a 3-clause BSD style license (see LICENSE.bsd3).
#
YARN_CLUSTER_METRICS = {
    metric: f"yarn_cluster_{metric}"
    for metric in (
        "appsSubmitted",
        "appsCompleted",
        "appsPending",
        "appsRunning",
        "appsFailed",
        "appsKilled",
        "totalMB",
        "availableMB",
        "allocatedMB",
        "availableVirtualCores",
        "allocatedVirtualCores",
        "totalNodes",
        "activeNodes",
        "lostNodes",
        "decommissioningNodes",
        "decommissionedNodes",
        "rebootedNodes",
        "shutdownNodes",
        "unhealthyNodes",
        "containersAllocated",
        "containersPending",
    )
}
YARN_NODES_METRICS = {
    metric: f"yarn_node_{metric}"
    for metric in (
        "numContainers",
        "usedMemoryMB",
        "availMemoryMB",
        "usedVirtualCores",
        "availableVirtualCores",
        "nodePhysicalMemoryMB",
        "nodeVirtualMemoryMB",
        "nodeCPUUsage",
        "containersCPUUsage",
        "aggregatedContainersPhysicalMemoryMB",
        "aggregatedContainersVirtualMemoryMB",
    )
}
SPARK_APPLICATION_GAUGE_METRICS = {
    metric: f"spark_job_{metric}"
    for metric in (
        "numActiveTasks",
        "numActiveStages",
    )
}
SPARK_APPLICATION_DIFF_METRICS = {
    metric: f"spark_job_diff_{metric}"
    for metric in (
        "numTasks",
        "numCompletedTasks",
        "numSkippedTasks",
        "numFailedTasks",
        "numCompletedStages",
        "numSkippedStages",
        "numFailedStages",
    )
}
SPARK_STAGE_METRICS = {
    metric: f"spark_stage_{metric}"
    for metric in (
        "numActiveTasks",
        "numCompleteTasks",
        "numFailedTasks",
        "executorRunTime",
        "inputBytes",
        "inputRecords",
        "outputBytes",
        "outputRecords",
        "shuffleReadBytes",
        "shuffleReadRecords",
        "shuffleWriteBytes",
        "shuffleWriteRecords",
        "memoryBytesSpilled",
        "diskBytesSpilled",
    )
}
SPARK_RDD_METRICS = {
    metric: f"spark_rdd_{metric}" for metric in ("numPartitions", "numCachedPartitions", "memoryUsed", "diskUsed")
}
SPARK_TASK_SUMMARY_METRICS = {
    metric: f"spark_stage_tasks_summary_{metric}"
    for metric in (
        "executorDeserializeTime",
        "executorDeserializeCpuTime",
        "executorRunTime",
        "executorCpuTime",
        "resultSize",
        "jvmGcTime",
        "resultSerializationTime",
        "gettingResultTime",
        "schedulerDelay",
        "peakExecutionMemory",
        "memoryBytesSpilled",
        "diskBytesSpilled",
    )
}
SPARK_STREAMING_STATISTICS_METRICS = {
    metric: f"spark_streaming_statistics_{metric}"
    for metric in (
        "avgInputRate",
        "avgProcessingTime",
        "avgSchedulingDelay",
        "avgTotalDelay",
        "batchDuration",
        "numActiveBatches",
        "numActiveReceivers",
        "numInactiveReceivers",
        "numProcessedRecords",
        "numReceivedRecords",
        "numReceivers",
        "numRetainedCompletedBatches",
        "numTotalCompletedBatches",
    )
}
SPARK_STRUCTURED_STREAMING_METRICS = {
    "inputRate-total": "spark_structured_streaming_input_rate",
    "latency": "spark_structured_streaming_latency",
    "processingRate-total": "spark_structured_streaming_processing_rate",
    "states-rowsTotal": "spark_structured_streaming_rows_count",
    "states-usedBytes": "spark_structured_streaming_used_bytes",
}
SPARK_STREAMING_BATCHES_METRICS = {
    "last_inputSize": "spark_app_streaming_inputSize_last",
    "last_processingTime": "spark_app_streaming_processingTime_last",
    "last_totalDelay": "spark_app_streaming_totalDelay_last",
    "last_batchDuration": "spark_app_streaming_batchDuration_last",
    "avg3_inputSize": "spark_app_streaming_inputSize_avg3",
    "max3_inputSize": "spark_app_streaming_inputSize_max3",
    "avg3_processingTime": "spark_app_streaming_processingTime_avg3",
    "max3_processingTime": "spark_app_streaming_processingTime_max3",
    "avg3_totalDelay": "spark_app_streaming_totalDelay_avg3",
    "max3_totalDelay": "spark_app_streaming_totalDelay_max3",
    "avg3_batchDuration": "spark_app_streaming_batchDuration_avg3",
    "avg10_inputSize": "spark_app_streaming_inputSize_avg10",
    "max10_inputSize": "spark_app_streaming_inputSize_max10",
    "avg10_processingTime": "spark_app_streaming_processingTime_avg10",
    "max10_processingTime": "spark_app_streaming_processingTime_max10",
    "avg10_totalDelay": "spark_app_streaming_totalDelay_avg10",
    "max10_totalDelay": "spark_app_streaming_totalDelay_max10",
    "avg10_batchDuration": "spark_app_streaming_batchDuration_avg10",
    "avg25_inputSize": "spark_app_streaming_inputSize_avg25",
    "max25_inputSize": "spark_app_streaming_inputSize_max25",
    "avg25_processingTime": "spark_app_streaming_processingTime_avg25",
    "max25_processingTime": "spark_app_streaming_processingTime_max25",
    "avg25_totalDelay": "spark_app_streaming_totalDelay_avg25",
    "max25_totalDelay": "spark_app_streaming_totalDelay_max25",
    "avg25_batchDuration": "spark_app_streaming_batchDuration_avg25",
}
SPARK_EXECUTORS_METRICS = {"count": "spark_executors_count", "activeCount": "spark_executors_active_count"}

SPARK_AGGREGATED_METRICS = {
    "failed_tasks": "spark_aggregated_stage_failed_tasks",
    "active_tasks": "spark_aggregated_stage_active_tasks",
    "pending_stages": "spark_aggregated_stage_pending_stages",
    "failed_stages": "spark_aggregated_stage_failed_stages",
    "active_stages": "spark_aggregated_stage_active_stages",
}

SPARK_RUNNING_APPS_COUNT_METRIC = {"running_applications": "spark_num_applications_running"}
