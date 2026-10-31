"""Bridge complete-only measured runs to the existing comparison record schema."""
from . import records
from .runner import condition_id


def make_record(result,*,model_role,baseline_id,dataset_revision,dataset_version,
                sample_sha256=None,report_mode='model_only'):
    if result.get('status')!='executed' or result.get('purpose')=='smoke':
        raise ValueError('quality_record_requires_complete_scored_run')
    if model_role not in ('m0','m1') or not baseline_id:
        raise ValueError('model_role_and_baseline_required')
    if model_role=='m0' and baseline_id!=result['experiment_id']:
        raise ValueError('baseline_record_must_reference_itself')
    common=dict(result['conditions'],report_mode=report_mode,
                rules='none' if report_mode=='model_only' else 'b-secret-rules-v1')
    policy=result['gateway'][report_mode]['normal_false_block']
    weights=result['artifacts_sha256'].get('model.safetensors') or result['artifacts_sha256']['pytorch_model.bin']
    record=records.record_from_score(result['score'],
        normal_false_block={'false_blocks':policy['false_blocks'],'normal_documents':policy['normal_documents'],
                            'rate':policy['rate']},
        experiment_id=result['experiment_id'],parent_experiment_id=None if model_role=='m0' else baseline_id,
        common_baseline_experiment_id=baseline_id,changed_factors=[] if model_role=='m0' else ['model_weights'],
        comparison_mode='controlled_weights_only',comparison_condition_id=condition_id(common),
        model_repo_revision_or_checkpoint_sha256=weights,dataset_revision=dataset_revision,
        split_and_service_test_version=dataset_version,sample_manifest_sha256=sample_sha256,
        seed=42,inference_configuration=common,label_mapping_version=condition_id({'labels':common['labels']}),
        metrics_protocol_version='char_span_exact_v1+B-gateway-one-reinspection-v1',
        threshold_version=condition_id({'thresholds':common['thresholds']}),
        rules_version=common['rules'],policy_version=common['policy'],
        chunking_and_overlap_version='gliner029-word-window-v1+'+common['splitter'],
        span_postprocessing_version='B-exact-char-dedup-v1',started_at_utc=result['started_at_utc'],
        finished_at_utc=result['finished_at_utc'],delta_vs_parent=None,delta_vs_common_baseline=None,
        report_mode=report_mode)
    record['execution_evidence']={'purpose':result['purpose'],'dataset_sha256':common['dataset_sha256'],
        'inference_status_counts':result['inference_status_counts'],'runtime_acceptance':None,
        'b_splitter_adoption':False,'scope':'A offline measured experiment; B production acceptance remains separate.'}
    records.validate_record(record)
    return record
