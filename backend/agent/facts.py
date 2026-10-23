"""Shared fact envelope; source-specific audit data stays in the tool record."""
from copy import deepcopy

UNITS = {'band_gap': 'eV', 'formation_energy_per_atom': 'eV/atom',
         'energy_above_hull': 'eV/atom', 'density': 'g/cm3',
         'space_group_number': '', 'space_group': '', 'crystal_system': ''}

def tool_facts(records):
    facts = []
    for record in records:
        result = record['result']
        if result.get('status') not in ('ok', 'success'):
            continue
        if record['name'] == 'search_materials':
            for row in result.get('candidates', []):
                for prop, unit in UNITS.items():
                    if row.get(prop) is not None:
                        facts.append({'object': row['material_id'], 'formula': row.get('formula'),
                                      'property': prop, 'value': row[prop], 'unit': unit,
                                      'basis': 'per_atom' if unit == 'eV/atom' else 'reported',
                                      'source': record['evidence_id'], 'source_type': 'tool_fact'})
        elif record['name'] == 'calculate':
            for item in result.get('provenance', {}).get('inputs', []):
                facts.append({**{k: item.get(k) for k in
                                 ('object', 'property', 'value', 'unit', 'basis', 'source', 'source_type')},
                              'path': item.get('path'), 'used_by': record['evidence_id'],
                              'origin': deepcopy(item.get('origin'))})
            for quantity in result.get('quantities', []):
                facts.append({**deepcopy(quantity), 'source': record['evidence_id']})
    return facts

def compact_result(record):
    """No altered values or missing candidates; eliminate repeated audit inputs."""
    result = record['result']
    if record['name'] != 'calculate':
        return {'evidence_id': record['evidence_id'], **deepcopy(result)}
    keys = ('status', 'operation', 'inputs', 'result', 'assumptions', 'message',
            'result_id', 'request_validation', 'execution')
    payload = {k: deepcopy(result[k]) for k in keys if k in result}
    if result.get('quantities'):
        keep = ('ref', 'object', 'formula', 'property', 'value', 'unit', 'basis', 'parents')
        payload['quantities'] = [{k: q[k] for k in keep if k in q} for q in result['quantities']]
    payload['evidence_id'] = record['evidence_id']
    payload['verification_scope'] = 'Arithmetic checked; physical assumptions require interpretation.'
    return payload
