"""Schema and singer message funtionalities"""
from typing import Dict, List

from datetime import datetime
import time
from decimal import Decimal
from dateutil import parser
from dateutil.parser import ParserError
from decimal import Decimal
from datetime import datetime, date
from singer import get_logger

from target_bigquery.exceptions import UnexpectedValueTypeException

LOGGER = get_logger('target_bigquery')

# max timestamp/datetime supported in BQ, used to reset all invalid dates that are beyond this value
MAX_TIMESTAMP = datetime.strptime('9999-12-31 23:59:59.999999', '%Y-%m-%d %H:%M:%S.%f')

# max time supported in BQ, used to reset all invalid times that are beyond this value
MAX_TIME = (datetime.strptime('23:59:59.999999','%H:%M:%S.%f') - datetime.min)


def get_schema_names_from_config(config: Dict) -> List:
    """Get list of target schema name from config"""
    default_target_schema = config.get('default_target_schema')
    schema_mapping = config.get('schema_mapping', {})
    schema_names = []

    if default_target_schema:
        schema_names.append(default_target_schema)

    if schema_mapping:
        for target in schema_mapping.values():
            schema_names.append(target.get('target_schema'))

    return schema_names


def adjust_timestamps_in_record(record: Dict, schema: Dict) -> None:
    """
    Goes through every field that is of type date/datetime/time and if its value is out of range,
    resets it to MAX value accordingly
    Args:
        record: record containing properties and values
        schema: json schema that has types of each property
    """

    # creating this internal function to avoid duplicating code and too many nested blocks.
    def reset_new_value(record: Dict, key: str, _format: str):
        if not isinstance(record[key], str):
            raise UnexpectedValueTypeException(
                f'Value {record[key]} of key "{key}" is not a string.')

        try:
            if _format == 'time':
                record[key] = parser.parse(record[key]).time()
            else:
                record[key] = parser.parse(record[key])
        except ParserError:
            LOGGER.warning('Parsing the %s "%s" in key "%s" has failed, thus defaulting to max '
                           'acceptable value of %s in BigQuery', _format, record[key], key, _format)
            record[key] = MAX_TIMESTAMP if _format != 'time' else MAX_TIME

    # traverse the schema looking for properties of some date type
    for key, value in record.items():
        if value is not None and key in schema['properties']:
            if 'anyOf' in schema['properties'][key]:
                for type_dict in schema['properties'][key]['anyOf']:
                    if 'string' in type_dict['type'] and type_dict.get('format', None) in {'date-time', 'time', 'date'}:
                        reset_new_value(record, key, type_dict['format'])
                        break
            elif 'properties' in schema['properties'][key]:
                for nested_key, nested_value in record[key].items():
                    if nested_value is not None and nested_key in schema['properties'][key]['properties']:
                        if 'string' in schema['properties'][key]['properties'][nested_key]['type'] and \
                                schema['properties'][key]['properties'][nested_key].get('format', None) in {'date-time', 'time', 'date'}:
                            reset_new_value(record[key], nested_key, schema['properties'][key]['properties'][nested_key]['format'])
            else:
                if 'string' in schema['properties'][key]['type'] and \
                        schema['properties'][key].get('format', None) in {'date-time', 'time', 'date'}:
                    reset_new_value(record, key, schema['properties'][key]['format'])


def float_to_decimal(value):
    """Walk the given data structure and turn all instances of float into double."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [float_to_decimal(child) for child in value]
    if isinstance(value, dict):
        return {k: float_to_decimal(v) for k, v in value.items()}
    return value


def add_metadata_values_to_record(record_message):
    """Populate metadata _sdc columns from incoming record message
    The location of the required attributes are fixed in the stream
    """
    def parse_datetime(dt):
        try:
            # TODO: figure out why we can get _sdc_deleted_at as both datetime and string objects
            if isinstance(dt, date):
                return dt

            if 'Z' in dt:
                return datetime.strptime(dt, '%Y-%m-%dT%H:%M:%S.%fZ')

            return datetime.fromisoformat('+'.join(dt.split('+')[:1]))
        except TypeError:
            return None

    extended_record = record_message['record']
    extended_record['_sdc_extracted_at'] = parse_datetime(record_message.get('time_extracted', datetime.now()))
    extended_record['_sdc_batched_at'] = datetime.now()
    extended_record['_sdc_deleted_at'] = parse_datetime(record_message.get('record', {}).get('_sdc_deleted_at'))

    return extended_record


def stream_name_to_dict(stream_name, separator='-'):
    """Transform stream name string to dictionary"""
    catalog_name = None
    schema_name = None
    table_name = stream_name

    # Schema and table name can be derived from stream if it's in <schema_nama>-<table_name> format
    s_parts = stream_name.split(separator)
    if len(s_parts) == 2:
        schema_name = s_parts[0]
        table_name = s_parts[1]
    if len(s_parts) > 2:
        catalog_name = s_parts[0]
        schema_name = s_parts[1]
        table_name = '_'.join(s_parts[2:])

    return {
        'catalog_name': catalog_name,
        'schema_name': schema_name,
        'table_name': table_name
    }
