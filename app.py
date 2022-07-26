from fileinput import filename
import os
from rdflib import Graph
from rdflib.util import guess_format
import requests

from flask import Flask, flash, request, render_template
from flask_swagger_ui import get_swaggerui_blueprint
from flask_wtf import FlaskForm
from flask_bootstrap import Bootstrap

from wtforms import URLField
from wtforms.validators import Optional
from pyshacl import validate

from config import config

from rmlmapper import find_data_source, replace_data_source, find_method_graph, count_rules_str

config_name = os.environ.get("APP_MODE") or "development"

app = Flask(__name__)
app.config.from_object(config[config_name])

bootstrap = Bootstrap(app)

SWAGGER_URL = "/api/docs"
API_URL = "/static/swagger.json"
swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={
        "app_name": "RDFConverter"
    }
)

app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)

from urllib.request import urlopen
from urllib.parse import urlparse, unquote
def open_file(uri=''):
    try:
        uri_parsed = urlparse(uri)
    except:
        print('not an uri - if local file add file:// as prefix')
        return None
    else:
        filename = unquote(uri_parsed.path).split('/')[-1]
        if uri_parsed.scheme in ['https', 'http']:
            filedata = requests.get(uri).text

        elif uri_parsed.scheme == 'file':
            filedata = open(unquote(uri_parsed.path), 'rb').read()
        else:
            print('unknown scheme {}'.format(uri_parsed.scheme))
            return None
        return filedata, filename

class StartForm(FlaskForm):
    mapping_url = URLField(
        'URL Field Mapping',
        #validators=[Optional()],
        description='Paste URL to a field mapping'
    )
    opt_data_csvw_url = URLField(
        'Optional: URL CSVW Json-LD',
        validators=[Optional()],
        description='Paste URL to a CSVW Json-LD'
    )
    shacl_url = URLField(
        'URL SHACL Shape Repository',
        validators=[Optional()],
        description='Paste URL to a SHACL Shape Repository'
    )
    opt_shacl_shape_url = URLField(
        'Optional: URL SHACL Shape',
        validators=[Optional()],
        description='Paste URL to a SHACL Shape'
    )

@app.route('/', methods=['GET', 'POST'])
def index():
    logo = './static/resources/MatOLab-Logo.svg'
    start_form = StartForm()
    message = ''
    result = ''
    conforms = None
        
    if request.method == 'POST' and start_form.validate():
        mapping_url = request.values.get('mapping_url')
        opt_data_csvw_url = request.values.get('opt_data_csvw_url')
        # shacl_url = request.values.get('shacl_url')
        opt_shacl_shape_url = request.values.get('opt_shacl_shape_url')
        if not mapping_url:
            flash('Must give a YARRRML file to convert!')
        
        request_body = {'mapping_url': mapping_url}
        if opt_data_csvw_url:
            request_body['data_url'] = opt_data_csvw_url

        result = requests.post('http://localhost:5000/api/createrdf', json=request_body).json()['graph']

        if opt_shacl_shape_url:
            conforms = requests.post('http://localhost:5000/api/rdfvalidator', json={'shapes_url': opt_shacl_shape_url, 'rdf_data': result}).json()['valid']


    return render_template(
        "index.html",
        logo=logo,
        start_form=start_form,
        message=message,
        result=result,
        conforms=conforms
        )

@app.route('/api/yarrrmltorml', methods=['POST'])
def translate():
    content = request.get_json()
    app.logger.info(f"POST /api/yarrrmltorml {content['url']}")
    filedata, filename = open_file(content['url'])
    rules = requests.post('http://yarrrml-parser:3000', data={'yarrrml': filedata}).text
    return rules

@app.route('/api/createrdf', methods=['POST'])
def create_rdf():
    content = request.get_json()
    app.logger.info(f"POST /api/yarrrmltorml {content['mapping_url']}")
    filedata, filename = open_file(content['mapping_url'])
    rml_rules = requests.post('http://yarrrml-parser:3000', data={'yarrrml': filedata}).text

    rml_graph = Graph()
    rml_graph.parse(data=rml_rules, format='ttl')

    data_url = find_data_source(rml_graph)
    # #method_url = find_method_graph(rml_graph)

    # replace rml source from mappingfile with local file 
    # because rmlmapper webapi does not work with remote sources
    replace_data_source(rml_graph, 'source.json')

    rml_rules_new = rml_graph.serialize(format='ttl')

    # replace data_url with specified override
    if 'data_url' in content.keys() and content['data_url']:
        rml_rules_new = rml_rules_new.replace(data_url, content['data_url'])
        data_url =content['data_url']
    
    data_url_content=requests.get(data_url).text
    
    # call rmlmapper webapi
    d = {'rml': rml_rules_new, 'sources': {'source.json': data_url_content}, 'serialization': 'turtle'}
    r = requests.post('http://rmlmapper:4000/execute', json=d)

    if r.status_code != 200:
        app.logger.error(r.text)
        return r.text, 400
    res = r.json()['output']
    
    data_graph = Graph()
    data_graph.parse(data_url, format=guess_format(data_url))
    #method_graph = Graph()
    #method_graph.parse(method_url, format=guess_format(method_url))

    mapping_graph = Graph()
    mapping_graph.parse(data=res, format='ttl')

    num_mappings_applied = len(mapping_graph)
    num_mappings_possible = count_rules_str(rml_rules)

    if not ('minimal' in content.keys() and content['minimal']):
        mapping_graph += data_graph
    #mapping_graph += method_graph
    app.logger.info(f'POST /api/joindata: {num_mappings_possible=}, {num_mappings_applied=}')
    return {'graph': mapping_graph.serialize(format='ttl'), 'num_mappings_applied': num_mappings_applied, 'num_mappings_skipped': num_mappings_possible-num_mappings_applied}

@app.route('/api/rdfvalidator', methods=['POST'])
def validate_rdf():

    content = request.get_json()
    try:
        if 'shapes_url' in content:
            shapes_data, filename = open_file(content['shapes_url'])
        else:
            shapes_data = content['shapes_data']

        if 'rdf_url' in content:
            rdf_data, filename = open_file(content['rdf_url'])
        else:
            rdf_data = content['rdf_data']
        
        shapes_graph = Graph()
        shapes_graph.parse(data=shapes_data, format=guess_format(content['shapes_url']) if 'shapes_url' in content else 'ttl')
        rdf_graph = Graph()
        rdf_graph.parse(data=rdf_data, format=guess_format(content['rdf_url']) if 'rdf_url' in content else 'ttl')
    except Exception as e:
        app.logger.error(e)
        return "Could not read graph!", 400

    try:
        conforms, g, _ = validate(
            rdf_graph,
            shacl_graph=shapes_graph,
            ont_graph=None,  # can use a Web URL for a graph containing extra ontological information
            inference='none',
            abort_on_first=False,
            allow_infos=False,
            allow_warnings=False,
            meta_shacl=False,
            advanced=False,
            js=False,
            debug=False)

    except Exception as e:
        app.logger.error(e)
        return str(e), 400

    app.logger.info(f'POST /api/rdfvalidator: {conforms=}')
    return {'valid': conforms, 'graph': g.serialize(format='ttl')}