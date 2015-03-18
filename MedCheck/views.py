"""
    File: views.py
    
    Author: William J. Bosl
    Children's Hospital Boston
    300 Longwood Avenue
    Boston, MA 02115
    Email: william.bosl@childrens.harvard.edu
    Web: http://chip.org

    Copyright (C) 2011 William Bosl, Children's Hospital Boston Informatics Program (CHIP)
    http://chip.org. 

    Purpose:
    
    This file is part of a Django-based SMArt application that implements
    a two-step test for medication adherence. It is intended to be used as
    a SMArt web application within the context of a SMArt container. See
    http://www.smarthealthit.org/ for detailed information about SMART applications.
        
    License information should go here.

    $Log: views.py,v $
"""
from django.http import HttpResponse
from django.template import Context
from django.template.loader import get_template
from django.template import RequestContext
from django.utils import simplejson 
from django.shortcuts import render_to_response, redirect

import ast
import datetime
import logging
import urllib
import mpr_monitor.settings as settings
import adherenceTests
from fhirclient import client
from fhirclient.models.medicationdispense import MedicationDispense
logging.basicConfig(level=logging.DEBUG)  # cf. .INFO or commented out

# SMART on FHIR Server Endpoint Configuration
_ENDPOINT = settings.ENDPOINT

# Global variables
ISO_8601_DATETIME = '%Y-%m-%d'
last_pill_dates = {}
Global_PATIENT_ID = 0
Global_ADHERE_VARS = 0

def _med_name(dispense):
    if dispense.medication and dispense.medication.resolved and dispense.medication.resolved.name:
        return dispense.medication.resolved.name
    if dispense.medication and dispense.medication.display:
        return dispense.medication.display
    raise Exception("Cannot determine medication name")

#===========================================
# The index page is the generally the first
# page to appear when the application is started.
#===========================================
def index(request):
    indexpage = get_template('index.html')

	# Declare global variables that may be modified here
    global Global_PATIENT_ID 
    global Global_ADHERE_VARS 

    smart = client.FHIRClient(state=request.session['client_state'])
    record_change_p = True
    patientID = smart.patient_id

    # Get the medication dispenses for this context
    dispenses = MedicationDispense.where().patient(patientID).perform(smart.server)

    pills = []

    for dispense in dispenses:
        d = dispense.dispense[0]
        name = d.medication.resolved.name
        
        assert d.status == 'completed'
        quant = list(ext.valueQuantity.value for ext in d.extension if ext.url == 'http://fhir-registry.smarthealthit.org/Profile/dispense#days-supply')[0]
        when = d.whenHandedOver.isostring
        pills.append((None,name,quant,when))

    birthday, patient_name = get_birthday_name(smart)
    drug = 'all'

    # We only want to call the adherence_check once for a specific patient
    if Global_PATIENT_ID == patientID:
        meds_flags, gaps, refill_data, refill_day = Global_ADHERE_VARS
    else:
        tests = adherenceTests.AdherenceTests()
        meds_flags, gaps, refill_data, refill_day = tests.allTests(pills, drug, birthday)		
        Global_ADHERE_VARS = [meds_flags, gaps, refill_data, refill_day]  # save the data for future needs
        Global_PATIENT_ID = patientID
        
	# Medication information will be displayed by drug class. Here we
	# sort all the patient's medications into drug classes defined
	# in this application.
    drug_class_array = {}
    for n in range(len(meds_flags)):
        drug_class_array[meds_flags[n][5]] = 1
    sorted_drug_class_list = sorted(drug_class_array.keys())
                  
	# Send these variables to the page for rendering
    variables = Context({
        'head_title': u'Medication Adherence Monitor',
        'patientID': patientID,
        'meds_flags': meds_flags,			# Contains all the data needed for tables and plotting 
        'media_root': settings.MEDIA_ROOT,
        'patient_name': patient_name,
        'drug_class_array': sorted_drug_class_list,
    })
    output = indexpage.render(variables)
    response = HttpResponse(output)
    return response

def launch(request):
    iss = request.GET.get('iss')
    
    if iss:
        _ENDPOINT.update({
            'api_base': iss,
            'auth_type': 'oauth2',
            'patient_id': None,
            'launch_token': request.GET.get('launch'),
            'redirect_uri': _ENDPOINT['app_base'] + "authorize.html"
        })
        smart = client.FHIRClient(settings=_ENDPOINT)
        auth_url = smart.authorize_url
        request.session['client_state']  = smart.state # TO DO: encrypt the state to protect app secrets
        return redirect(auth_url)
        
    fhirServiceUrl = request.GET.get('fhirServiceUrl')
        
    if fhirServiceUrl:
        _ENDPOINT['api_base'] = fhirServiceUrl
        _ENDPOINT['patient_id'] = request.GET.get('patientId')
        _ENDPOINT['auth_type'] = 'none'
        smart = client.FHIRClient(settings=_ENDPOINT)
        redirect_url = _ENDPOINT['app_base'] + "index.html"
        request.session['client_state']  = smart.state # TO DO: encrypt the state to protect app secrets
        return redirect(redirect_url)
    
def authorize(request):    
    smart = client.FHIRClient(state=request.session['client_state'])
    smart.handle_callback(request.build_absolute_uri())
    request.session['client_state'] = smart.state
    return redirect('index.html')

#===================================================
# Creates data and serves information about 
# adherence for specific medications.
#===================================================
def risk(request):
    """ This function creates data and serves detailed  
    information about adherence for specific medications."""
	
	# Declare global variables that may be modified here
    global Global_PATIENT_ID 
    global Global_ADHERE_VARS 
	
    # Get the name of the drug if a specific one was requested.
    # The default is 'all' drugs.
    drug = request.GET.get('drug', 'all')
       
    # Current context information
    smart = client.FHIRClient(state=request.session['client_state'])

    # Get the medication dispenses for this context
    dispenses = MedicationDispense.where().patient(smart.patient_id).perform(smart.server)

    pills = []
        
    for dispense in dispenses:
        d = dispense.dispense[0]
        name = d.medication.resolved.name
        
        assert d.status == 'completed'
        quant = list(ext.valueQuantity.value for ext in d.extension if ext.url == 'http://fhir-registry.smarthealthit.org/Profile/dispense#days-supply')[0]
        when = d.whenHandedOver.isostring
        pills.append((None,name,quant,when))
    
    # The the fulfillment gap and MPR prediction data    
    meds_flags, gaps, refill_data, refill_day = Global_ADHERE_VARS

    names = []
    if drug == 'all':   # get all the drugs for this patient
        for pill in pills: 
            name = pill[1]
            names.append(name)
            d = pill[3]
    else: # only use the specified drug name
        meds_flags_new = []
        names.append(drug)      
        for item in meds_flags:
            if drug == item[0]:
                meds_flags_new.append(item)
        meds_flags = meds_flags_new 
                
    ad_data = []
    med_names = []

    for n in names:
        d = {}
        d["title"] = str(n)
        med_names.append(n)
        d["subtitle"] = 'adherence'
        d["measures"] = [1.0]
        ad_data.append(d)
           
    drug_class_array = {}
    for n in range(len(meds_flags)):
        drug_class_array[meds_flags[n][5]] = 1
    sorted_drug_class_array = sorted(drug_class_array.keys())
                            
    # Determine width and height of chart by the number of drugs to be shown
    width = 400
    height = 100
    if len(names) == 1:
        width = 500
        height = 200
    
    variables = RequestContext(request, {
                'head_title': u'Predicted 1-year medication possession ratio (MPR)',
                'med_names': med_names,
                'meds_flags': meds_flags,
                'refill_day': simplejson.dumps(refill_day),
                'refill': simplejson.dumps(refill_data),
                'gaps': simplejson.dumps(gaps),
                'width': width,
                'height': height,
                'drug_class_array': sorted_drug_class_array,
                })     
    response = render_to_response("risk.html", context_instance=variables )
    return HttpResponse(response)

#===================================================
# Page to display information about the MPR
# Monitor app.
#===================================================
def about(request):
    """ This function creates a page with information about the MPR Monitor app."""
	
    page = get_template('about.html')
    variables = Context({ })
    output = page.render(variables)
    return HttpResponse(output)

#===================================================
# This function creates a page that gives instructions
# for using the MPR Monitor app.
#===================================================
def choose_med(request):
    """ This function creates a page with instructions for the MPR Monitor app."""

    page = get_template('choose_med.html')
    variables = Context({ })
	# Render the page
    output = page.render(variables)
    return HttpResponse(output)

#===================================================
# Function to get birthday and patient name from
# the client records and return them.
#===================================================
def get_birthday_name(client):
    """Function to get birthday and patient name from the client records and return them."""
	    
    patient = client.patient
    patient_name = client.human_name(patient.name[0])
    birthday = patient.birthDate.isostring
    return birthday, patient_name
