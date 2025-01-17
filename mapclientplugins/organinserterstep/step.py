
"""
MAP Client Plugin Step
"""
import csv
import json
import os

from PySide2 import QtCore, QtGui, QtWidgets

from mapclient.mountpoints.workflowstep import WorkflowStepMountPoint
from mapclientplugins.organinserterstep.configuredialog import ConfigureDialog

from opencmiss.utils.zinc.general import ChangeManager
from opencmiss.utils.zinc.field import findOrCreateFieldGroup, findOrCreateFieldCoordinates,\
    findOrCreateFieldStoredString
from opencmiss.zinc.context import Context
from opencmiss.zinc.field import Field
from opencmiss.zinc.node import Node
from opencmiss.zinc.result import RESULT_OK

from scaffoldfitter.fitter import Fitter
from scaffoldfitter.fitterstepalign import FitterStepAlign
from scaffoldfitter.fitterstepfit import FitterStepFit


class OrganInserter(object):
    def __init__(self, input_model_file, input_data_files, output_directory):
        self._input_data_files = input_data_files
        marker_coordinates = MarkerCoordinates(input_model_file, output_directory)
        # organ_transformer = OrganTransformer(input_data_files, marker_coordinates.output_filename(), output_directory)
        # self._output_filename = organ_transformer.output_filename()

        self.write_annotations(output_directory)

        self._output_filenames = []
        for file in input_data_files:
            if 'colon' in file.lower():
                self._output_filenames.append(file)
                self.add_organ_group(file)
            else:
                organ_transformer = OrganTransformer(file, marker_coordinates.output_filename(), output_directory)
                self._output_filenames.append(organ_transformer.output_filename())
                self.add_organ_group(organ_transformer.output_filename())

    def get_output_file_name(self):
        return self._output_filenames

    def get_organ_name(self, filename):
        organsList = ['lung', 'heart', 'brainstem', 'stomach', 'bladder']
        filename_base = os.path.basename(filename).split('.')[0]
        for organ_name in organsList:
            if organ_name in filename_base.lower():
                return organ_name
        else:
            return filename_base

    def write_annotations(self, output_directory):
        DOI = ["https://doi.org/10.26275/yibc-wyu2", "https://doi.org/10.26275/dqpf-gqdt",
               "https://doi.org/10.26275/rets-qdch", "https://doi.org/10.26275/dqpf-gqdt",
               "https://doi.org/10.26275/yum2-z4uf", "https://doi.org/10.26275/xq3h-ba2b", "colon"]
        organ_names = ['whole-body']
        for filename in self._input_data_files:
            organ_names.append(self.get_organ_name(filename))

        annotation_file = os.path.join(output_directory, 'organinserter_annotations.csv')
        with open(annotation_file, 'w', newline='') as fout:
            writer = csv.writer(fout)
            writer.writerow(['Organ name', 'Source', 'File name', 'Transformed file name'])
            writer.writerow([organ_names[0], DOI[0], 'whole_body.exf', 'whole_body.exf'])
            for c, filename in enumerate(self._input_data_files):
                filenamebase = os.path.basename(filename)
                writer.writerow([organ_names[c+1], DOI[c+1], filenamebase, filenamebase.split('.')[0]+'_transfromed_fit1.exf'])

    def add_organ_group(self, filename):
        context = Context('organGroup')
        region = context.createRegion()
        region.readFile(filename)
        field_module = region.getFieldmodule()
        mesh = field_module.findMeshByDimension(3)
        with ChangeManager(field_module):
            field_group = field_module.createFieldGroup()
            organ_name = self.get_organ_name(filename)
            field_group.setName(organ_name)
            field_group.setSubelementHandlingMode(field_group.SUBELEMENT_HANDLING_MODE_FULL)
            element_group = field_group.createFieldElementGroup(mesh)
            mesh_group = element_group.getMeshGroup()
            is_organ = field_module.createFieldConstant(1)
            mesh_group.addElementsConditional(is_organ)
            sir = region.createStreaminformationRegion()
            srm = sir.createStreamresourceMemory()
            sir.setResourceGroupName(srm, organ_name)
            # sir.setResourceFieldNames(srm, fieldNames)
            region.write(sir)
            region.writeFile(filename)


class BaseOutputFile(object):

    def __init__(self):
        self._output_filename = None

    def output_filename(self):
        return self._output_filename


class OrganTransformer(BaseOutputFile):

    def __init__(self, input_zinc_model_file, input_zinc_data_file, output_directory):
        super().__init__()
        self._fitter = Fitter(input_zinc_model_file, input_zinc_data_file)
        self._fitter.load()
        self.set_model_coordinates_field()

        file_basename = os.path.basename(input_zinc_model_file).split('.')[0]
        filename = file_basename + '_transformed'
        path = output_directory
        self._output_filename = os.path.join(path, filename)

        self._currentFitterStep = FitterStepAlign()
        self._fitter.addFitterStep(self._currentFitterStep)  # Future: , lastFitterStep
        self._currentFitterStep.setAlignMarkers(True)
        self._currentFitterStep.run(modelFileNameStem=self._output_filename)

        self._currentFitterStep = FitterStepFit()
        self._fitter.addFitterStep(self._currentFitterStep)  # Future: , lastFitterStep
        self._currentFitterStep.setGroupStrainPenalty(None, [0.001])
        self._currentFitterStep.setGroupCurvaturePenalty(None, [200.0])
        self._currentFitterStep.setGroupDataWeight(None, 1000.0)

        print("Transforming organ ({}) ... It may take a minute".format(file_basename))
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        self._currentFitterStep.run(modelFileNameStem=self._output_filename)
        self._output_filename = self._output_filename + '_fit1.exf'
        QtWidgets.QApplication.restoreOverrideCursor()
        print('Transformation is done')

    def set_model_coordinates_field(self):
        field_module = self._fitter.getFieldmodule()
        modelCoordinatesFieldName = "coordinates"
        field = field_module.findFieldByName(modelCoordinatesFieldName)
        if field.isValid():
            self._fitter.setModelCoordinatesFieldByName(modelCoordinatesFieldName)


class MarkerCoordinates(BaseOutputFile):
    def __init__(self, input_scaffold_file, output_directory):
        super().__init__()
        self._context = Context('markerBodyCoordinates')
        self._region = self._context.createRegion()
        self._region.setName('bodyRegion')
        self._field_module = self._region.getFieldmodule()
        self._scaffold_file = input_scaffold_file
        self._model_coordinates_field = None
        self._marker_region = None

        self._load()
        self._get_marker_coordinates()
        marker_region = self._get_marker_region()
        self._save(marker_region, output_directory)

    def _discover_coordinate_fields(self):
        field = None
        if self._model_coordinates_field:
            field = self._field_module.findFieldByName(self._model_coordinates_field)
        else:
            mesh = self._get_highest_dimension_mesh()
            element = mesh.createElementiterator().next()
            if element.isValid():
                field_cache = self._field_module.createFieldcache()
                field_cache.setElement(element)
                field_iter = self._field_module.createFielditerator()
                field = field_iter.next()
                while field.isValid():
                    if field.isTypeCoordinate() and (field.getNumberOfComponents() == 3) \
                            and (field.castFiniteElement().isValid()):
                        if field.isDefinedAtLocation(field_cache):
                            break
                    field = field_iter.next()
                else:
                    field = None
        if field:
            self._set_model_coordinates_field(field)

    def get_marker_fields(self):
        marker_name = False
        marker_location_name = False
        marker_group_name = False
        field_iter = self._field_module.createFielditerator()
        field = field_iter.next()
        while field.isValid():
            field_name = field.getName()
            if 'marker' in field_name.lower():
                if 'name' in field_name.lower():
                    marker_name = field_name
                elif 'location' in field_name.lower():
                    marker_location_name = field_name
                elif '.' not in field_name:
                    marker_group_name = field_name
            field = field_iter.next()
        if all([marker_name, marker_location_name, marker_group_name]):
            return marker_location_name, marker_name, marker_group_name
        else:
            raise AssertionError('Could not find marker fields')

    def _get_highest_dimension_mesh(self):
        for d in range(2, -1, -1):
            mesh = self._mesh[d]
            if mesh.getSize() > 0:
                return mesh
        return None

    def _load(self):
        result = self._region.readFile(self._scaffold_file)
        assert result == RESULT_OK, "Failed to load model file" + str(self._scaffold_file)
        self._mesh = [self._field_module.findMeshByDimension(d + 1) for d in range(3)]
        self._discover_coordinate_fields()

    def _save(self, region, output_directory):
        filename = os.path.basename(self._scaffold_file).split('.')[0] + '_marker_coordinates.exnode'
        path = output_directory
        self._output_filename = os.path.join(path, filename)
        region.writeFile(self._output_filename)

    def _get_marker_coordinates(self):
        field_cache = self._field_module.createFieldcache()
        nodes = self._field_module.findNodesetByFieldDomainType(Field.DOMAIN_TYPE_NODES)

        marker_location_name, marker_name, marker_group_name = self.get_marker_fields()

        markerLocation = self._field_module.findFieldByName(marker_location_name)
        markerName = self._field_module.findFieldByName(marker_name)
        markerGroup = self._field_module.findFieldByName(marker_group_name)

        self._marker_region = self._region.createRegion()
        marker_fieldmodule = self._marker_region.getFieldmodule()
        temp_nodes = marker_fieldmodule.findNodesetByFieldDomainType(Field.DOMAIN_TYPE_NODES)
        marker_fieldCache = marker_fieldmodule.createFieldcache()
        marker_data_coordinates = findOrCreateFieldCoordinates(marker_fieldmodule, name="marker_data_coordinates",
                                                               components_count=3)
        marker_data_name = findOrCreateFieldStoredString(marker_fieldmodule, name="marker_data_name")
        marker_data_group = findOrCreateFieldGroup(marker_fieldmodule, name="marker")
        marker_data_nodesGroup = marker_data_group.createFieldNodeGroup(temp_nodes).getNodesetGroup()

        markerTemplateInternal = temp_nodes.createNodetemplate()
        markerTemplateInternal.defineField(marker_data_name)
        markerTemplateInternal.defineField(marker_data_coordinates)
        markerTemplateInternal.setValueNumberOfVersions(marker_data_coordinates, -1, Node.VALUE_LABEL_VALUE, 1)

        markerNodes = None
        if markerGroup.isValid():
            markerGroup = markerGroup.castGroup()
            markerNodeGroup = markerGroup.getFieldNodeGroup(nodes)
            if markerNodeGroup.isValid():
                markerNodes = markerNodeGroup.getNodesetGroup()

        if markerLocation.isValid() and markerName.isValid():
            with ChangeManager(marker_fieldmodule):
                marker_coordinates = self._field_module.createFieldEmbedded(self._model_coordinates_field,
                                                                            markerLocation)
                nodeIter = markerNodes.createNodeiterator()
                node = nodeIter.next()
                while node.isValid():
                    marker_node = temp_nodes.createNode(node.getIdentifier(), markerTemplateInternal)
                    marker_data_nodesGroup.addNode(marker_node)

                    marker_fieldCache.setNode(marker_node)
                    field_cache.setNode(node)
                    result, x = marker_coordinates.evaluateReal(field_cache, 3)
                    result = marker_data_coordinates.setNodeParameters(marker_fieldCache, -1, Node.VALUE_LABEL_VALUE, 1, x)
                    if result == RESULT_OK:
                        name = markerName.evaluateString(field_cache)
                        if name:
                            marker_data_name.assignString(marker_fieldCache, name)
                    node = nodeIter.next()

    def _set_model_coordinates_field(self, model_coordinates_field: Field):
        finite_element_field = model_coordinates_field.castFiniteElement()
        assert finite_element_field.isValid() and (finite_element_field.getNumberOfComponents() == 3)
        self._model_coordinates_field = finite_element_field

    def _get_marker_region(self):
        return self._marker_region


class OrganInserterStep(WorkflowStepMountPoint):
    """
    Skeleton step which is intended to be a helpful starting point
    for new steps.
    """

    def __init__(self, location):
        super(OrganInserterStep, self).__init__('Organ Inserter', location)
        self._configured = False  # A step cannot be executed until it has been configured.
        self._category = 'Registration'
        # Add any other initialisation code here:
        self._icon = QtGui.QImage(':/organinserterstep/images/registration.png')
        # Ports:
        self.addPort(('http://physiomeproject.org/workflow/1.0/rdf-schema#port',
                      'http://physiomeproject.org/workflow/1.0/rdf-schema#uses',
                      'http://physiomeproject.org/workflow/1.0/rdf-schema#file_location'))
        self.addPort(('http://physiomeproject.org/workflow/1.0/rdf-schema#port',
                      'http://physiomeproject.org/workflow/1.0/rdf-schema#uses-list-of',
                      'http://physiomeproject.org/workflow/1.0/rdf-schema#file_location'))
        self.addPort(('http://physiomeproject.org/workflow/1.0/rdf-schema#port',
                      'http://physiomeproject.org/workflow/1.0/rdf-schema#provides',
                      'http://physiomeproject.org/workflow/1.0/rdf-schema#file_location'))
        # Port data:
        self._port0_inputZincModelFile = None  # http://physiomeproject.org/workflow/1.0/rdf-schema#file_location
        self._port1_inputZincDataFile = None  # http://physiomeproject.org/workflow/1.0/rdf-schema#file_location
        self._port2_output_marker_data_file = None  # http://physiomeproject.org/workflow/1.0/rdf-schema#file_location
        # Config:
        self._config = {'identifier': ''}

        self._organ_inserter = None

    def execute(self):
        """
        Add your code here that will kick off the execution of the step.
        Make sure you call the _doneExecution() method when finished.  This method
        may be connected up to a button in a widget for example.
        """
        # Put your execute step code here before calling the '_doneExecution' method.

        self._organ_inserter = OrganInserter(self._port0_inputZincModelFile, self._port1_inputZincDataFile,
                                             self._location)
        self._port2_output_marker_data_file = self._organ_inserter.get_output_file_name()

        self._doneExecution()

    def setPortData(self, index, dataIn):
        """
        Add your code here that will set the appropriate objects for this step.
        The index is the index of the port in the port list.  If there is only one
        uses port for this step then the index can be ignored.

        :param index: Index of the port to return.
        :param dataIn: The data to set for the port at the given index.
        """
        if index == 0:
            self._port0_inputZincModelFile = dataIn  # http://physiomeproject.org/workflow/1.0/rdf-schema#file_location
        elif index == 1:
            self._port1_inputZincDataFile = dataIn  # http://physiomeproject.org/workflow/1.0/rdf-schema#file_location

    def getPortData(self, index):
        """
        Add your code here that will return the appropriate objects for this step.
        The index is the index of the port in the port list.  If there is only one
        provides port for this step then the index can be ignored.

        :param index: Index of the port to return.
        """
        files = []
        for file in self._port2_output_marker_data_file:
            files.append(os.path.realpath(os.path.join(self._location, file)))
        return files  # http://physiomeproject.org/workflow/1.0/rdf-schema#multiple_file_locations
        # return self._port2_output_marker_data_file  # http://physiomeproject.org/workflow/1.0/rdf-schema#file_location

    def configure(self):
        """
        This function will be called when the configure icon on the step is
        clicked.  It is appropriate to display a configuration dialog at this
        time.  If the conditions for the configuration of this step are complete
        then set:
            self._configured = True
        """
        dlg = ConfigureDialog(self._main_window)
        dlg.identifierOccursCount = self._identifierOccursCount
        dlg.setConfig(self._config)
        dlg.validate()
        dlg.setModal(True)

        if dlg.exec_():
            self._config = dlg.getConfig()

        self._configured = dlg.validate()
        self._configuredObserver()

    def getIdentifier(self):
        """
        The identifier is a string that must be unique within a workflow.
        """
        return self._config['identifier']

    def setIdentifier(self, identifier):
        """
        The framework will set the identifier for this step when it is loaded.
        """
        self._config['identifier'] = identifier

    def serialize(self):
        """
        Add code to serialize this step to string.  This method should
        implement the opposite of 'deserialize'.
        """
        return json.dumps(self._config, default=lambda o: o.__dict__, sort_keys=True, indent=4)

    def deserialize(self, string):
        """
        Add code to deserialize this step from string.  This method should
        implement the opposite of 'serialize'.

        :param string: JSON representation of the configuration in a string.
        """
        self._config.update(json.loads(string))

        d = ConfigureDialog()
        d.identifierOccursCount = self._identifierOccursCount
        d.setConfig(self._config)
        self._configured = d.validate()
