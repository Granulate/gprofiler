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
    "appsSubmitted": {"name": "yarn:cluster:appsSubmitted", "documentation": "", "labels": []},
    "appsCompleted": {"name": "yarn:cluster:appsCompleted", "documentation": "", "labels": []},
    "appsPending": {"name": "yarn:cluster:appsPending", "documentation": "", "labels": []},
    "appsRunning": {"name": "yarn:cluster:appsRunning", "documentation": "", "labels": []},
    "appsFailed": {"name": "yarn:cluster:appsFailed", "documentation": "", "labels": []},
    "appsKilled": {"name": "yarn:cluster:appsKilled", "documentation": "", "labels": []},
    "totalMB": {"name": "yarn:cluster:totalMB", "documentation": "", "labels": []},
    "availableMB": {"name": "yarn:cluster:availableMB", "documentation": "", "labels": []},
    "allocatedMB": {"name": "yarn:cluster:allocatedMB", "documentation": "", "labels": []},
    "availableVirtualCores": {"name": "yarn:cluster:availableVirtualCores", "documentation": "", "labels": []},
    "allocatedVirtualCores": {"name": "yarn:cluster:allocatedVirtualCores", "documentation": "", "labels": []},
    "totalNodes": {"name": "yarn:cluster:totalNodes", "documentation": "", "labels": []},
    "activeNodes": {"name": "yarn:cluster:activeNodes", "documentation": "", "labels": []},
    "lostNodes": {"name": "yarn:cluster:lostNodes", "documentation": "", "labels": []},
    "decommissioningNodes": {"name": "yarn:cluster:decommissioningNodes", "documentation": "", "labels": []},
    "decommissionedNodes": {"name": "yarn:cluster:decommissionedNodes", "documentation": "", "labels": []},
    "rebootedNodes": {"name": "yarn:cluster:rebootedNodes", "documentation": "", "labels": []},
    "shutdownNodes": {"name": "yarn:cluster:shutdownNodes", "documentation": "", "labels": []},
    "unhealthyNodes": {"name": "yarn:cluster:unhealthyNodes", "documentation": "", "labels": []},
    "containersAllocated": {"name": "yarn:cluster:containersAllocated", "documentation": "", "labels": []},
    "containersPending": {"name": "yarn:cluster:containersPending", "documentation": "", "labels": []},
}
YARN_ORIGINAL_CLUSTER_METRICS = {
    "availableMB": {"name": "yarn:cluster:originalAvailableMB", "documentation": "", "labels": []}
}
YARN_NODES_METRICS = {
    "numContainers": {"name": "yarn:node:numContainers", "documentation": "", "labels": YARN_NODES_LABELS},
    "usedMemoryMB": {"name": "yarn:node:usedMemoryMB", "documentation": "", "labels": YARN_NODES_LABELS},
    "availMemoryMB": {"name": "yarn:node:availMemoryMB", "documentation": "", "labels": YARN_NODES_LABELS},
    "usedVirtualCores": {"name": "yarn:node:usedVirtualCores", "documentation": "", "labels": YARN_NODES_LABELS},
    "availableVirtualCores": {
        "name": "yarn:node:availableVirtualCores",
        "documentation": "",
        "labels": YARN_NODES_LABELS,
    },
    "nodePhysicalMemoryMB": {
        "name": "yarn:node:nodePhysicalMemoryMB",
        "documentation": "",
        "labels": YARN_NODES_LABELS,
    },
    "nodeVirtualMemoryMB": {"name": "yarn:node:nodeVirtualMemoryMB", "documentation": "", "labels": YARN_NODES_LABELS},
    "nodeCPUUsage": {"name": "yarn:node:nodeCPUUsage", "documentation": "", "labels": YARN_NODES_LABELS},
    "containersCPUUsage": {"name": "yarn:node:containersCPUUsage", "documentation": "", "labels": YARN_NODES_LABELS},
    "aggregatedContainersPhysicalMemoryMB": {
        "name": "yarn:node:aggregatedContainersPhysicalMemoryMB",
        "documentation": "",
        "labels": YARN_NODES_LABELS,
    },
    "aggregatedContainersVirtualMemoryMB": {
        "name": "yarn:node:aggregatedContainersVirtualMemoryMB",
        "documentation": "",
        "labels": YARN_NODES_LABELS,
    },
}
DRIVER_CLUSTER_METRICS = {
    "appsSubmitted": {"name": "yarn:cluster:appsSubmitted", "documentation": "", "labels": []},
    "appsCompleted": {"name": "yarn:cluster:appsCompleted", "documentation": "", "labels": []},
    "appsPending": {"name": "yarn:cluster:appsPending", "documentation": "", "labels": []},
    "appsRunning": {"name": "yarn:cluster:appsRunning", "documentation": "", "labels": []},
    "appsFailed": {"name": "yarn:cluster:appsFailed", "documentation": "", "labels": []},
    "appsKilled": {"name": "yarn:cluster:appsKilled", "documentation": "", "labels": []},
}
SPARK_APPLICATION_GAUGE_METRICS = {
    "numActiveTasks": {
        "name": "spark:job:num_active_tasks",
        "documentation": "Number of currently active tasks",
        "labels": SPARK_APP_LABELS,
    },
    "numActiveStages": {
        "name": "spark:job:num_active_stages",
        "documentation": "Number of total active stages",
        "labels": SPARK_APP_LABELS,
    },
}
SPARK_APPLICATION_DIFF_METRICS = {
    "numTasks": {
        "name": "spark:job:diff_num_tasks",
        "documentation": "Diff between queries of total submitted tasks count",
        "labels": SPARK_APP_LABELS,
    },
    "numCompletedTasks": {
        "name": "spark:job:diff_num_completed_tasks",
        "documentation": "Diff between queries of total completed tasks count",
        "labels": SPARK_APP_LABELS,
    },
    "numSkippedTasks": {
        "name": "spark:job:diff_num_skipped_tasks",
        "documentation": "Diff between queries of total skipped tasks count",
        "labels": SPARK_APP_LABELS,
    },
    "numFailedTasks": {
        "name": "spark:job:diff_num_failed_tasks",
        "documentation": "Diff between queries of total failed tasks count",
        "labels": SPARK_APP_LABELS,
    },
    "numCompletedStages": {
        "name": "spark:job:diff_num_completed_stages",
        "documentation": "Diff between queries of total completed stages count",
        "labels": SPARK_APP_LABELS,
    },
    "numSkippedStages": {
        "name": "spark:job:diff_num_skipped_stages",
        "documentation": "Diff between queries of total skipped stages count",
        "labels": SPARK_APP_LABELS,
    },
    "numFailedStages": {
        "name": "spark:job:diff_num_failed_stages",
        "documentation": "Diff between queries of total failed stages count",
        "labels": SPARK_APP_LABELS,
    },
}
SPARK_STAGE_METRICS = {
    "numActiveTasks": {"name": "spark:stage:num_active_tasks", "documentation": "", "labels": SPARK_STAGE_LABELS},
    "numCompleteTasks": {"name": "spark:stage:num_complete_tasks", "documentation": "", "labels": SPARK_STAGE_LABELS},
    "numFailedTasks": {"name": "spark:stage:num_failed_tasks", "documentation": "", "labels": SPARK_STAGE_LABELS},
    "executorRunTime": {"name": "spark:stage:executor_run_time", "documentation": "", "labels": SPARK_STAGE_LABELS},
    "inputBytes": {"name": "spark:stage:input_bytes", "documentation": "", "labels": SPARK_STAGE_LABELS},
    "inputRecords": {"name": "spark:stage:input_records", "documentation": "", "labels": SPARK_STAGE_LABELS},
    "outputBytes": {"name": "spark:stage:output_bytes", "documentation": "", "labels": SPARK_STAGE_LABELS},
    "outputRecords": {"name": "spark:stage:output_records", "documentation": "", "labels": SPARK_STAGE_LABELS},
    "shuffleReadBytes": {"name": "spark:stage:shuffle_read_bytes", "documentation": "", "labels": SPARK_STAGE_LABELS},
    "shuffleReadRecords": {
        "name": "spark:stage:shuffle_read_records",
        "documentation": "",
        "labels": SPARK_STAGE_LABELS,
    },
    "shuffleWriteBytes": {
        "name": "spark:stage:shuffle_write_bytes",
        "documentation": "",
        "labels": SPARK_STAGE_LABELS,
    },
    "shuffleWriteRecords": {
        "name": "spark:stage:shuffle_write_records",
        "documentation": "",
        "labels": SPARK_STAGE_LABELS,
    },
    "memoryBytesSpilled": {
        "name": "spark:stage:memory_bytes_spilled",
        "documentation": "",
        "labels": SPARK_STAGE_LABELS,
    },
    "diskBytesSpilled": {
        "name": "spark:stage:disk_bytes_spilled",
        "documentation": "",
        "labels": SPARK_STAGE_LABELS,
    },
}
SPARK_RDD_METRICS = {
    "numPartitions": {"name": "spark:rdd:num_partitions", "documentation": "", "labels": SPARK_RDD_LABELS},
    "numCachedPartitions": {
        "name": "spark:rdd:num_cached_partitions",
        "documentation": "",
        "labels": SPARK_RDD_LABELS,
    },
    "memoryUsed": {"name": "spark:rdd:memory_used", "documentation": "", "labels": SPARK_RDD_LABELS},
    "diskUsed": {"name": "spark:rdd:disk_used", "documentation": "", "labels": SPARK_RDD_LABELS},
}
SPARK_EXECUTOR_TEMPLATE_METRICS = {
    "rddBlocks": ("spark:%s:rdd_blocks", ""),
    "memoryUsed": ("spark:%s:memory_used", ""),
    "diskUsed": ("spark:%s:disk_used", ""),
    "activeTasks": ("spark:%s:active_tasks", ""),
    "failedTasks": ("spark:%s:failed_tasks", ""),
    "completedTasks": ("spark:%s:completed_tasks", ""),
    "totalTasks": ("spark:%s:total_tasks", ""),
    "totalDuration": ("spark:%s:total_duration", ""),
    "totalInputBytes": ("spark:%s:total_input_bytes", ""),
    "totalShuffleRead": ("spark:%s:total_shuffle_read", ""),
    "totalShuffleWrite": ("spark:%s:total_shuffle_write", ""),
    "maxMemory": ("spark:%s:max_memory", ""),
}
SPARK_DRIVER_METRICS = {
    key: {"name": value[0] % "driver", "documentation": value[1], "labels": SPARK_DRIVER_LABELS}
    for key, value in SPARK_EXECUTOR_TEMPLATE_METRICS.items()
}
SPARK_EXECUTOR_METRICS = {
    key: {"name": value[0] % "executor", "documentation": value[1], "labels": SPARK_EXECUTORS_LABELS}
    for key, value in SPARK_EXECUTOR_TEMPLATE_METRICS.items()
}
SPARK_EXECUTOR_METRICS["totalGCTime"] = {
    "name": "spark:executor:total_gc_time",
    "documentation": "",
    "labels": SPARK_EXECUTORS_LABELS,
}
SPARK_EXECUTOR_LEVEL_METRICS = {
    key: {"name": value[0] % "executor_id", "documentation": value[1], "labels": SPARK_EXECUTOR_LABELS}
    for key, value in SPARK_EXECUTOR_TEMPLATE_METRICS.items()
}
SPARK_TASK_SUMMARY_METRICS = {
    "executorDeserializeTime": {
        "name": "spark:stage:tasks_summary:executor_deserialize_time",
        "documentation": "",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "executorDeserializeCpuTime": {
        "name": "spark:stage:tasks_summary:executor_deserialize_cpu_time",
        "documentation": "",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "executorRunTime": {
        "name": "spark:stage:tasks_summary:executor_run_time",
        "documentation": "",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "executorCpuTime": {
        "name": "spark:stage:tasks_summary:executor_cpu_time",
        "documentation": "",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "resultSize": {
        "name": "spark:stage:tasks_summary:result_size",
        "documentation": "",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "jvmGcTime": {
        "name": "spark:stage:tasks_summary:jvm_gc_time",
        "documentation": "",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "resultSerializationTime": {
        "name": "spark:stage:tasks_summary:result_serialization_time",
        "documentation": "",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "gettingResultTime": {
        "name": "spark:stage:tasks_summary:getting_result_time",
        "documentation": "",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "schedulerDelay": {
        "name": "spark:stage:tasks_summary:scheduler_delay",
        "documentation": "",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "peakExecutionMemory": {
        "name": "spark:stage:tasks_summary:peak_execution_memory",
        "documentation": "",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "memoryBytesSpilled": {
        "name": "spark:stage:tasks_summary:memory_bytes_spilled",
        "documentation": "",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
    "diskBytesSpilled": {
        "name": "spark:stage:tasks_summary:disk_bytes_spilled",
        "documentation": "",
        "labels": SPARK_TASK_SUMMERY_LABELS,
    },
}
SPARK_APPLICATIONS_TIME = {
    "elapsedTime": {
        "name": "spark:application:completion_time",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
}
SPARK_STREAMING_STATISTICS_METRICS = {
    "avgInputRate": {
        "name": "spark:streaming:statistics:avg_input_rate",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "avgProcessingTime": {
        "name": "spark:streaming:statistics:avg_processing_time",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "avgSchedulingDelay": {
        "name": "spark:streaming:statistics:avg_scheduling_delay",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "avgTotalDelay": {
        "name": "spark:streaming:statistics:avg_total_delay",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "batchDuration": {
        "name": "spark:streaming:statistics:batch_duration",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "numActiveBatches": {
        "name": "spark:streaming:statistics:num_active_batches",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "numActiveReceivers": {
        "name": "spark:streaming:statistics:num_active_receivers",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "numInactiveReceivers": {
        "name": "spark:streaming:statistics:num_inactive_receivers",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "numProcessedRecords": {
        "name": "spark:streaming:statistics:num_processed_records",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "numReceivedRecords": {
        "name": "spark:streaming:statistics:num_received_records",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "numReceivers": {
        "name": "spark:streaming:statistics:num_receivers",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "numRetainedCompletedBatches": {
        "name": "spark:streaming:statistics:num_retained_completed_batches",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "numTotalCompletedBatches": {
        "name": "spark:streaming:statistics:num_total_completed_batches",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
}
SPARK_STRUCTURED_STREAMING_METRICS = {
    "inputRate-total": {
        "name": "spark:structured_streaming:input_rate",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "latency": {"name": "spark:structured_streaming:latency", "documentation": "", "labels": SPARK_APPS_LABELS},
    "processingRate-total": {
        "name": "spark:structured_streaming:processing_rate",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "states-rowsTotal": {
        "name": "spark:structured_streaming:rows_count",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
    "states-usedBytes": {
        "name": "spark:structured_streaming:used_bytes",
        "documentation": "",
        "labels": SPARK_APPS_LABELS,
    },
}
SPARK_STREAMING_BATCHES_METRICS = {
    "last_inputSize": {
        "name": "spark:app:streaming:inputSize:last",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "last_processingTime": {
        "name": "spark:app:streaming:processingTime:last",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "last_totalDelay": {
        "name": "spark:app:streaming:totalDelay:last",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "last_batchDuration": {
        "name": "spark:app:streaming:batchDuration:last",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg3_inputSize": {
        "name": "spark:app:streaming:inputSize:avg3",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "max3_inputSize": {
        "name": "spark:app:streaming:inputSize:max3",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg3_processingTime": {
        "name": "spark:app:streaming:processingTime:avg3",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "max3_processingTime": {
        "name": "spark:app:streaming:processingTime:max3",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg3_totalDelay": {
        "name": "spark:app:streaming:totalDelay:avg3",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "max3_totalDelay": {
        "name": "spark:app:streaming:totalDelay:max3",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg3_batchDuration": {
        "name": "spark:app:streaming:batchDuration:avg3",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg10_inputSize": {
        "name": "spark:app:streaming:inputSize:avg10",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "max10_inputSize": {
        "name": "spark:app:streaming:inputSize:max10",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg10_processingTime": {
        "name": "spark:app:streaming:processingTime:avg10",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "max10_processingTime": {
        "name": "spark:app:streaming:processingTime:max10",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg10_totalDelay": {
        "name": "spark:app:streaming:totalDelay:avg10",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "max10_totalDelay": {
        "name": "spark:app:streaming:totalDelay:max10",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg10_batchDuration": {
        "name": "spark:app:streaming:batchDuration:avg10",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg25_inputSize": {
        "name": "spark:app:streaming:inputSize:avg25",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "max25_inputSize": {
        "name": "spark:app:streaming:inputSize:max25",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg25_processingTime": {
        "name": "spark:app:streaming:processingTime:avg25",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "max25_processingTime": {
        "name": "spark:app:streaming:processingTime:max25",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg25_totalDelay": {
        "name": "spark:app:streaming:totalDelay:avg25",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "max25_totalDelay": {
        "name": "spark:app:streaming:totalDelay:max25",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
    "avg25_batchDuration": {
        "name": "spark:app:streaming:batchDuration:avg25",
        "documentation": "",
        "labels": SPARK_BATCH_LABELS,
    },
}
EXECUTORS_COUNT = {"name": "spark:executors:count", "documentation": "", "labels": ["app_name", "app_id"]}
ACTIVE_EXECUTORS_COUNT = {
    "name": "spark:executors:active_count",
    "documentation": "",
    "labels": ["app_name", "app_id"],
}
YARN_APPS_ELAPSED_TIME_RANGES = [3, 10, 25]
YARN_APPLICATIONS_ELAPSED_TIME = {
    "last_elapsedTime": {
        "name": "yarn:cluster:appElapsedTime:last",
        "documentation": "",
    },
}
for n in YARN_APPS_ELAPSED_TIME_RANGES:
    YARN_APPLICATIONS_ELAPSED_TIME.update(
        {
            f"avg{n}_elapsedTime": {"name": f"yarn:cluster:appElapsedTime:avg{n}", "documentation": ""},
            f"max{n}_elapsedTime": {
                "name": f"yarn:cluster:appElapsedTime:max{n}",
                "documentation": "",
            },
        }
    )
