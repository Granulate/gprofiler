YARN_NODES_LABELS = ["node_hostname"]
SPARK_APPS_LABELS = ["app_name", "app_id"]
SPARK_APP_LABELS = ["app_name", "app_id"]
SPARK_BATCH_LABELS = ["app_name", "app_id"]
SPARK_STAGE_LABELS = ["app_name", "app_id", "status", "stage_id"]
SPARK_TASK_SUMMERY_LABELS = ["app_name", "app_id", "status", "stage_id", "quantile"]
SPARK_RDD_LABELS = ["app_name", "app_id"]
SPARK_DRIVER_LABELS = ["app_name", "app_id"]
SPARK_EXECUTORS_LABELS = ["app_name", "app_id"]
SPARK_EXECUTOR_LABELS = ["app_name", "app_id", "executor_id"]

YARN_CLUSTER_METRICS = {
    "appsSubmitted": {"name": "yarn_cluster_appsSubmitted", "labels": []},
    "appsCompleted": {"name": "yarn_cluster_appsCompleted", "labels": []},
    "appsPending": {"name": "yarn_cluster_appsPending", "labels": []},
    "appsRunning": {"name": "yarn_cluster_appsRunning", "labels": []},
    "appsFailed": {"name": "yarn_cluster_appsFailed", "labels": []},
    "appsKilled": {"name": "yarn_cluster_appsKilled", "labels": []},
    "totalMB": {"name": "yarn_cluster_totalMB", "labels": []},
    "availableMB": {"name": "yarn_cluster_availableMB", "labels": []},
    "allocatedMB": {"name": "yarn_cluster_allocatedMB", "labels": []},
    "availableVirtualCores": {
        "name": "yarn_cluster_availableVirtualCores",
        "labels": [],
    },
    "allocatedVirtualCores": {
        "name": "yarn_cluster_allocatedVirtualCores",
        "labels": [],
    },
    "totalNodes": {"name": "yarn_cluster_totalNodes", "labels": []},
    "activeNodes": {"name": "yarn_cluster_activeNodes", "labels": []},
    "lostNodes": {"name": "yarn_cluster_lostNodes", "labels": []},
    "decommissioningNodes": {"name": "yarn_cluster_decommissioningNodes", "labels": []},
    "decommissionedNodes": {"name": "yarn_cluster_decommissionedNodes", "labels": []},
    "rebootedNodes": {"name": "yarn_cluster_rebootedNodes", "labels": []},
    "shutdownNodes": {"name": "yarn_cluster_shutdownNodes", "labels": []},
    "unhealthyNodes": {"name": "yarn_cluster_unhealthyNodes", "labels": []},
    "containersAllocated": {"name": "yarn_cluster_containersAllocated", "labels": []},
    "containersPending": {"name": "yarn_cluster_containersPending", "labels": []},
}
YARN_NODES_METRICS = {
    "numContainers": {"name": "yarn_node_numContainers", "labels": YARN_NODES_LABELS},
    "usedMemoryMB": {"name": "yarn_node_usedMemoryMB", "labels": YARN_NODES_LABELS},
    "availMemoryMB": {"name": "yarn_node_availMemoryMB", "labels": YARN_NODES_LABELS},
    "usedVirtualCores": {
        "name": "yarn_node_usedVirtualCores",
        "labels": YARN_NODES_LABELS,
    },
    "availableVirtualCores": {
        "name": "yarn_node_availableVirtualCores",
        "labels": YARN_NODES_LABELS,
    },
    "nodePhysicalMemoryMB": {
        "name": "yarn_node_nodePhysicalMemoryMB",
        "labels": YARN_NODES_LABELS,
    },
    "nodeVirtualMemoryMB": {
        "name": "yarn_node_nodeVirtualMemoryMB",
        "labels": YARN_NODES_LABELS,
    },
    "nodeCPUUsage": {"name": "yarn_node_nodeCPUUsage", "labels": YARN_NODES_LABELS},
    "containersCPUUsage": {
        "name": "yarn_node_containersCPUUsage",
        "labels": YARN_NODES_LABELS,
    },
    "aggregatedContainersPhysicalMemoryMB": {
        "name": "yarn_node_aggregatedContainersPhysicalMemoryMB",
        "labels": YARN_NODES_LABELS,
    },
    "aggregatedContainersVirtualMemoryMB": {
        "name": "yarn_node_aggregatedContainersVirtualMemoryMB",
        "labels": YARN_NODES_LABELS,
    },
}
DRIVER_CLUSTER_METRICS = {
    "appsSubmitted": {"name": "yarn_cluster_appsSubmitted", "labels": []},
    "appsCompleted": {"name": "yarn_cluster_appsCompleted", "labels": []},
    "appsPending": {"name": "yarn_cluster_appsPending", "labels": []},
    "appsRunning": {"name": "yarn_cluster_appsRunning", "labels": []},
    "appsFailed": {"name": "yarn_cluster_appsFailed", "labels": []},
    "appsKilled": {"name": "yarn_cluster_appsKilled", "labels": []},
}
SPARK_APPLICATION_GAUGE_METRICS = {
    "numActiveTasks": {
        "name": "spark_job_num_active_tasks",
        "documentation": "Number of currently active tasks",
        "labels": SPARK_APP_LABELS,
    },
    "numActiveStages": {
        "name": "spark_job_num_active_stages",
        "documentation": "Number of total active stages",
        "labels": SPARK_APP_LABELS,
    },
}
SPARK_APPLICATION_DIFF_METRICS = {
    "numTasks": {
        "name": "spark_job_diff_num_tasks",
        "documentation": "Diff between queries of total submitted tasks count",
        "labels": SPARK_APP_LABELS,
    },
    "numCompletedTasks": {
        "name": "spark_job_diff_num_completed_tasks",
        "documentation": "Diff between queries of total completed tasks count",
        "labels": SPARK_APP_LABELS,
    },
    "numSkippedTasks": {
        "name": "spark_job_diff_num_skipped_tasks",
        "documentation": "Diff between queries of total skipped tasks count",
        "labels": SPARK_APP_LABELS,
    },
    "numFailedTasks": {
        "name": "spark_job_diff_num_failed_tasks",
        "documentation": "Diff between queries of total failed tasks count",
        "labels": SPARK_APP_LABELS,
    },
    "numCompletedStages": {
        "name": "spark_job_diff_num_completed_stages",
        "documentation": "Diff between queries of total completed stages count",
        "labels": SPARK_APP_LABELS,
    },
    "numSkippedStages": {
        "name": "spark_job_diff_num_skipped_stages",
        "documentation": "Diff between queries of total skipped stages count",
        "labels": SPARK_APP_LABELS,
    },
    "numFailedStages": {
        "name": "spark_job_diff_num_failed_stages",
        "documentation": "Diff between queries of total failed stages count",
        "labels": SPARK_APP_LABELS,
    },
}
SPARK_STAGE_METRICS = {
    "numActiveTasks": {
        "name": "spark_stage_num_active_tasks",
        "labels": SPARK_STAGE_LABELS,
    },
    "numCompleteTasks": {
        "name": "spark_stage_num_complete_tasks",
        "labels": SPARK_STAGE_LABELS,
    },
    "numFailedTasks": {
        "name": "spark_stage_num_failed_tasks",
        "labels": SPARK_STAGE_LABELS,
    },
    "executorRunTime": {
        "name": "spark_stage_executor_run_time",
        "labels": SPARK_STAGE_LABELS,
    },
    "inputBytes": {"name": "spark_stage_input_bytes", "labels": SPARK_STAGE_LABELS},
    "inputRecords": {"name": "spark_stage_input_records", "labels": SPARK_STAGE_LABELS},
    "outputBytes": {"name": "spark_stage_output_bytes", "labels": SPARK_STAGE_LABELS},
    "outputRecords": {
        "name": "spark_stage_output_records",
        "labels": SPARK_STAGE_LABELS,
    },
    "shuffleReadBytes": {
        "name": "spark_stage_shuffle_read_bytes",
        "labels": SPARK_STAGE_LABELS,
    },
    "shuffleReadRecords": {
        "name": "spark_stage_shuffle_read_records",
        "labels": SPARK_STAGE_LABELS,
    },
    "shuffleWriteBytes": {
        "name": "spark_stage_shuffle_write_bytes",
        "labels": SPARK_STAGE_LABELS,
    },
    "shuffleWriteRecords": {
        "name": "spark_stage_shuffle_write_records",
        "labels": SPARK_STAGE_LABELS,
    },
    "memoryBytesSpilled": {
        "name": "spark_stage_memory_bytes_spilled",
        "labels": SPARK_STAGE_LABELS,
    },
    "diskBytesSpilled": {
        "name": "spark_stage_disk_bytes_spilled",
        "labels": SPARK_STAGE_LABELS,
    },
}
SPARK_RDD_METRICS = {
    "numPartitions": {"name": "spark_rdd_num_partitions", "labels": SPARK_RDD_LABELS},
    "numCachedPartitions": {
        "name": "spark_rdd_num_cached_partitions",
        "labels": SPARK_RDD_LABELS,
    },
    "memoryUsed": {"name": "spark_rdd_memory_used", "labels": SPARK_RDD_LABELS},
    "diskUsed": {"name": "spark_rdd_disk_used", "labels": SPARK_RDD_LABELS},
}
SPARK_EXECUTOR_TEMPLATE_METRICS = {
    "rddBlocks": ("spark_%s_rdd_blocks", ""),
    "memoryUsed": ("spark_%s_memory_used", ""),
    "diskUsed": ("spark_%s_disk_used", ""),
    "activeTasks": ("spark_%s_active_tasks", ""),
    "failedTasks": ("spark_%s_failed_tasks", ""),
    "completedTasks": ("spark_%s_completed_tasks", ""),
    "totalTasks": ("spark_%s_total_tasks", ""),
    "totalDuration": ("spark_%s_total_duration", ""),
    "totalInputBytes": ("spark_%s_total_input_bytes", ""),
    "totalShuffleRead": ("spark_%s_total_shuffle_read", ""),
    "totalShuffleWrite": ("spark_%s_total_shuffle_write", ""),
    "maxMemory": ("spark_%s_max_memory", ""),
}
SPARK_DRIVER_METRICS = {
    key: {
        "name": value[0] % "driver",
        "documentation": value[1],
        "labels": SPARK_DRIVER_LABELS,
    }
    for key, value in SPARK_EXECUTOR_TEMPLATE_METRICS.items()
}
SPARK_EXECUTOR_METRICS = {
    key: {
        "name": value[0] % "executor",
        "documentation": value[1],
        "labels": SPARK_EXECUTORS_LABELS,
    }
    for key, value in SPARK_EXECUTOR_TEMPLATE_METRICS.items()
}
SPARK_EXECUTOR_LEVEL_METRICS = {
    key: {
        "name": value[0] % "executor_id",
        "documentation": value[1],
        "labels": SPARK_EXECUTOR_LABELS,
    }
    for key, value in SPARK_EXECUTOR_TEMPLATE_METRICS.items()
}
SPARK_TASK_SUMMARY_METRICS = {
    "executorDeserializeTime": {
        "name": "spark_stage_tasks_summary_executor_deserialize_time",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "executorDeserializeCpuTime": {
        "name": "spark_stage_tasks_summary_executor_deserialize_cpu_time",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "executorRunTime": {
        "name": "spark_stage_tasks_summary_executor_run_time",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "executorCpuTime": {
        "name": "spark_stage_tasks_summary_executor_cpu_time",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "resultSize": {
        "name": "spark_stage_tasks_summary_result_size",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "jvmGcTime": {
        "name": "spark_stage_tasks_summary_jvm_gc_time",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "resultSerializationTime": {
        "name": "spark_stage_tasks_summary_result_serialization_time",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "gettingResultTime": {
        "name": "spark_stage_tasks_summary_getting_result_time",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "schedulerDelay": {
        "name": "spark_stage_tasks_summary_scheduler_delay",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "peakExecutionMemory": {
        "name": "spark_stage_tasks_summary_peak_execution_memory",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "memoryBytesSpilled": {
        "name": "spark_stage_tasks_summary_memory_bytes_spilled",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "diskBytesSpilled": {
        "name": "spark_stage_tasks_summary_disk_bytes_spilled",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
}
SPARK_APPLICATIONS_TIME = {
    "elapsedTime": {
        "name": "spark_application_completion_time",
        "labels": SPARK_APPS_LABELS,
    },
}
SPARK_STREAMING_STATISTICS_METRICS = {
    "avgInputRate": {
        "name": "spark_streaming_statistics_avg_input_rate",
        "labels": SPARK_APPS_LABELS,
    },
    "avgProcessingTime": {
        "name": "spark_streaming_statistics_avg_processing_time",
        "labels": SPARK_APPS_LABELS,
    },
    "avgSchedulingDelay": {
        "name": "spark_streaming_statistics_avg_scheduling_delay",
        "labels": SPARK_APPS_LABELS,
    },
    "avgTotalDelay": {
        "name": "spark_streaming_statistics_avg_total_delay",
        "labels": SPARK_APPS_LABELS,
    },
    "batchDuration": {
        "name": "spark_streaming_statistics_batch_duration",
        "labels": SPARK_APPS_LABELS,
    },
    "numActiveBatches": {
        "name": "spark_streaming_statistics_num_active_batches",
        "labels": SPARK_APPS_LABELS,
    },
    "numActiveReceivers": {
        "name": "spark_streaming_statistics_num_active_receivers",
        "labels": SPARK_APPS_LABELS,
    },
    "numInactiveReceivers": {
        "name": "spark_streaming_statistics_num_inactive_receivers",
        "labels": SPARK_APPS_LABELS,
    },
    "numProcessedRecords": {
        "name": "spark_streaming_statistics_num_processed_records",
        "labels": SPARK_APPS_LABELS,
    },
    "numReceivedRecords": {
        "name": "spark_streaming_statistics_num_received_records",
        "labels": SPARK_APPS_LABELS,
    },
    "numReceivers": {
        "name": "spark_streaming_statistics_num_receivers",
        "labels": SPARK_APPS_LABELS,
    },
    "numRetainedCompletedBatches": {
        "name": "spark_streaming_statistics_num_retained_completed_batches",
        "labels": SPARK_APPS_LABELS,
    },
    "numTotalCompletedBatches": {
        "name": "spark_streaming_statistics_num_total_completed_batches",
        "labels": SPARK_APPS_LABELS,
    },
}
SPARK_STRUCTURED_STREAMING_METRICS = {
    "inputRate-total": {
        "name": "spark_structured_streaming_input_rate",
        "labels": SPARK_APPS_LABELS,
    },
    "latency": {
        "name": "spark_structured_streaming_latency",
        "labels": SPARK_APPS_LABELS,
    },
    "processingRate-total": {
        "name": "spark_structured_streaming_processing_rate",
        "labels": SPARK_APPS_LABELS,
    },
    "states-rowsTotal": {
        "name": "spark_structured_streaming_rows_count",
        "labels": SPARK_APPS_LABELS,
    },
    "states-usedBytes": {
        "name": "spark_structured_streaming_used_bytes",
        "labels": SPARK_APPS_LABELS,
    },
}
SPARK_STREAMING_BATCHES_METRICS = {
    "last_inputSize": {
        "name": "spark_app_streaming_inputSize_last",
        "labels": SPARK_BATCH_LABELS,
    },
    "last_processingTime": {
        "name": "spark_app_streaming_processingTime_last",
        "labels": SPARK_BATCH_LABELS,
    },
    "last_totalDelay": {
        "name": "spark_app_streaming_totalDelay_last",
        "labels": SPARK_BATCH_LABELS,
    },
    "last_batchDuration": {
        "name": "spark_app_streaming_batchDuration_last",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg3_inputSize": {
        "name": "spark_app_streaming_inputSize_avg3",
        "labels": SPARK_BATCH_LABELS,
    },
    "max3_inputSize": {
        "name": "spark_app_streaming_inputSize_max3",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg3_processingTime": {
        "name": "spark_app_streaming_processingTime_avg3",
        "labels": SPARK_BATCH_LABELS,
    },
    "max3_processingTime": {
        "name": "spark_app_streaming_processingTime_max3",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg3_totalDelay": {
        "name": "spark_app_streaming_totalDelay_avg3",
        "labels": SPARK_BATCH_LABELS,
    },
    "max3_totalDelay": {
        "name": "spark_app_streaming_totalDelay_max3",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg3_batchDuration": {
        "name": "spark_app_streaming_batchDuration_avg3",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg10_inputSize": {
        "name": "spark_app_streaming_inputSize_avg10",
        "labels": SPARK_BATCH_LABELS,
    },
    "max10_inputSize": {
        "name": "spark_app_streaming_inputSize_max10",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg10_processingTime": {
        "name": "spark_app_streaming_processingTime_avg10",
        "labels": SPARK_BATCH_LABELS,
    },
    "max10_processingTime": {
        "name": "spark_app_streaming_processingTime_max10",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg10_totalDelay": {
        "name": "spark_app_streaming_totalDelay_avg10",
        "labels": SPARK_BATCH_LABELS,
    },
    "max10_totalDelay": {
        "name": "spark_app_streaming_totalDelay_max10",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg10_batchDuration": {
        "name": "spark_app_streaming_batchDuration_avg10",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg25_inputSize": {
        "name": "spark_app_streaming_inputSize_avg25",
        "labels": SPARK_BATCH_LABELS,
    },
    "max25_inputSize": {
        "name": "spark_app_streaming_inputSize_max25",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg25_processingTime": {
        "name": "spark_app_streaming_processingTime_avg25",
        "labels": SPARK_BATCH_LABELS,
    },
    "max25_processingTime": {
        "name": "spark_app_streaming_processingTime_max25",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg25_totalDelay": {
        "name": "spark_app_streaming_totalDelay_avg25",
        "labels": SPARK_BATCH_LABELS,
    },
    "max25_totalDelay": {
        "name": "spark_app_streaming_totalDelay_max25",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg25_batchDuration": {
        "name": "spark_app_streaming_batchDuration_avg25",
        "labels": SPARK_BATCH_LABELS,
    },
}
EXECUTORS_COUNT = {"name": "spark_executors_count", "labels": ["app_name", "app_id"]}
ACTIVE_EXECUTORS_COUNT = {
    "name": "spark_executors_active_count",
    "labels": ["app_name", "app_id"],
}
