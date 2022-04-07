# This python autopsy module will export the SAM Regiatry Hive and then call
# the command line version of the SAMParse program.  A sqlite database that
# contains the SAM information is created then imported into the extracted
# view section of Autopsy.
#
# Contact: Mark McKinnon [Mark [dot] McKinnon <at> Davenport [dot] edu]
#
# This is free and unencumbered software released into the public domain.
#
# Anyone is free to copy, modify, publish, use, compile, sell, or
# distribute this software, either in source code form or as a compiled
# binary, for any purpose, commercial or non-commercial, and by any
# means.
#
# In jurisdictions that recognize copyright laws, the author or authors
# of this software dedicate any and all copyright interest in the
# software to the public domain. We make this dedication for the benefit
# of the public at large and to the detriment of our heirs and
# successors. We intend this dedication to be an overt act of
# relinquishment in perpetuity of all present and future rights to this
# software under copyright law.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.

# SAMParse module to parse the SAM registry hive.
# June 2016
# 
# Comments 
#   Version 1.0 - Initial version - June 2016
#   Version 1.1 - Added Custom artifacts and attributes - August 30, 2016
#   Version 1.2 - Added Support for Linux and cleanup code
# 

import jarray
import inspect
import os
import subprocess

from java.lang import Class
from java.lang import System
from java.sql  import DriverManager, SQLException
from java.util.logging import Level
from java.io import File
from org.sleuthkit.datamodel import SleuthkitCase
from org.sleuthkit.datamodel import AbstractFile
from org.sleuthkit.datamodel import ReadContentInputStream
from org.sleuthkit.datamodel import BlackboardArtifact
from org.sleuthkit.datamodel import BlackboardAttribute
from org.sleuthkit.autopsy.ingest import IngestModule
from org.sleuthkit.autopsy.ingest.IngestModule import IngestModuleException
from org.sleuthkit.autopsy.ingest import DataSourceIngestModule
from org.sleuthkit.autopsy.ingest import IngestModuleFactoryAdapter
from org.sleuthkit.autopsy.ingest import IngestModuleIngestJobSettings
from org.sleuthkit.autopsy.ingest import IngestModuleIngestJobSettingsPanel
from org.sleuthkit.autopsy.ingest import IngestMessage
from org.sleuthkit.autopsy.ingest import IngestServices
from org.sleuthkit.autopsy.ingest import ModuleDataEvent
from org.sleuthkit.autopsy.coreutils import Logger
from org.sleuthkit.autopsy.coreutils import PlatformUtil
from org.sleuthkit.autopsy.casemodule import Case
from org.sleuthkit.autopsy.casemodule.services import Services
from org.sleuthkit.autopsy.casemodule.services import FileManager
from org.sleuthkit.autopsy.datamodel import ContentUtils


# Factory that defines the name and details of the module and allows Autopsy
# to create instances of the modules that will do the analysis.
class ParseSAMIngestModuleFactory(IngestModuleFactoryAdapter):

    def __init__(self):
        self.settings = None

    moduleName = "SAMParse"
    
    def getModuleDisplayName(self):
        return self.moduleName
    
    def getModuleDescription(self):
        return "Parses The SAM Registry Hive"
    
    def getModuleVersionNumber(self):
        return "1.0"
    
    def isDataSourceIngestModuleFactory(self):
        return True

    def createDataSourceIngestModule(self, ingestOptions):
        return ParseSAMIngestModule(self.settings)

# Data Source-level ingest module.  One gets created per data source.
class ParseSAMIngestModule(DataSourceIngestModule):

    _logger = Logger.getLogger(ParseSAMIngestModuleFactory.moduleName)

    def log(self, level, msg):
        self._logger.logp(level, self.__class__.__name__, inspect.stack()[1][3], msg)

    def __init__(self, settings):
        self.context = None
        self.local_settings = settings
        self.List_Of_Events = []

    # Where any setup and configuration is done
    # 'context' is an instance of org.sleuthkit.autopsy.ingest.IngestJobContext.
    def startUp(self, context):
        self.context = context

        # Get path to EXE based on where this script is run from.
        # Assumes EXE is in same folder as script
        # Verify it is there before any ingest starts
        # Check what OS you are running from 
        if PlatformUtil.isWindowsOS():
            self.path_to_exe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samparse.exe")
            if not os.path.exists(self.path_to_exe):
                raise IngestModuleException("Windows Executable was not found in module folder")
        elif PlatformUtil.getOSName() == 'Linux':
            self.path_to_exe = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Samparse')
            if not os.path.exists(self.path_to_exe):
                raise IngestModuleException("Linux Executable was not found in module folder")

        
        # Throw an IngestModule.IngestModuleException exception if there was a problem setting up
        # raise IngestModuleException(IngestModule(), "Oh No!")
        pass

    # Where the analysis is done.
    def process(self, dataSource, progressBar):

        # we don't know how much work there is yet
        progressBar.switchToIndeterminate()
        
        # Set the database to be read to the once created by the SAM parser program
        skCase = Case.getCurrentCase().getSleuthkitCase();
        fileManager = Case.getCurrentCase().getServices().getFileManager()
        files = fileManager.findFiles(dataSource, "SAM", "config")
        numFiles = len(files)
        self.log(Level.INFO, "found " + str(numFiles) + " files")
        progressBar.switchToDeterminate(numFiles)
        fileCount = 0;
        lclDbPath = ''

        try:
             self.log(Level.INFO, "Begin Create New Artifacts")
             artID_sam = skCase.addArtifactType( "TSK_SAM", "SAM File")
        except:		
             self.log(Level.INFO, "Artifacts Creation Error, some artifacts may not exist now. ==> ")

        artID_sam = skCase.getArtifactTypeID("TSK_SAM")
        artID_sam_evt = skCase.getArtifactType("TSK_SAM")

	# Create SAM directory in temp directory, if it exists then continue on processing		
        Temp_Dir = Case.getCurrentCase().getTempDirectory()
        self.log(Level.INFO, "create Directory " + Temp_Dir)
        try:
            temp_dir = os.path.join(Temp_Dir, "SAM")
            os.mkdir(temp_dir)
        except:
            self.log(Level.INFO, "SAM Directory already exists " + temp_dir)
			
        # Write out SAM file to the temp directory
        for file in files:
            
            # Check if the user pressed cancel while we were busy
            if self.context.isJobCancelled():
                return IngestModule.ProcessResult.OK

            #self.log(Level.INFO, "Processing file: " + file.getName())
            fileCount += 1

            # Save the File locally in the temp folder. use file id as name to reduce collisions
            lclDbPath = os.path.join(temp_dir, file.getName())
            ContentUtils.writeToFile(file, File(lclDbPath))
                        


        # Run the EXE, saving output to a sqlite database
        sam_database = os.path.join(temp_dir, "SAM.db3")
        sam_file = os.path.join(temp_dir, "SAM")
        self.log(Level.INFO, self.path_to_exe + " " + sam_file + " " + sam_database)
        subprocess.Popen([self.path_to_exe, sam_file, sam_database]).communicate()[0]   
               
        # Extract data from the database and create attributes
        for file in files:	
           # Open the DB using JDBC
           self.log(Level.INFO, "Path the SAM database file created ==> " + sam_database)
           try: 
               Class.forName("org.sqlite.JDBC").newInstance()
               dbConn = DriverManager.getConnection("jdbc:sqlite:%s"  % sam_database)
           except SQLException as e:
               self.log(Level.INFO, "Could not open database file (not SQLite) " + sam_database + " (" + e.getMessage() + ")")
               return IngestModule.ProcessResult.OK
            
           # Query the contacts table in the database and get all columns. 
           try:
               stmt = dbConn.createStatement()
               resultSet = stmt.executeQuery("Select tbl_name from SQLITE_MASTER; ")
               self.log(Level.INFO, "query SQLite Master table")
           except SQLException as e:
               self.log(Level.INFO, "Error querying database for SAM table (" + e.getMessage() + ")")
               return IngestModule.ProcessResult.OK

             
           # Cycle through each row and create artifacts
           while resultSet.next():
               try: 
                   self.log(Level.INFO, "Result (" + resultSet.getString("tbl_name") + ")")
                   table_name = resultSet.getString("tbl_name")
                   #self.log(Level.INFO, "Result get information from table " + resultSet.getString("tbl_name") + " ")
                   SQL_String_1 = "Select * from " + table_name + ";"
                   SQL_String_2 = "PRAGMA table_info('" + table_name + "')"
                   #self.log(Level.INFO, SQL_String_1)
                   #self.log(Level.INFO, SQL_String_2)
				   
                   Column_Names = []
                   Column_Types = []
                   resultSet2  = stmt.executeQuery(SQL_String_2)
                   while resultSet2.next(): 
                      Column_Names.append(resultSet2.getString("name").upper())
                      Column_Types.append(resultSet2.getString("type"))
                      if resultSet2.getString("type") == "text":
                          try:
                              attID_ex1 = skCase.addArtifactAttributeType("TSK_" + resultSet2.getString("name").upper(), BlackboardAttribute.TSK_BLACKBOARD_ATTRIBUTE_VALUE_TYPE.STRING, resultSet2.getString("name"))
                              #self.log(Level.INFO, "attribure id for " + "TSK_" + resultSet2.getString("name") + " == " + str(attID_ex1))
                          except:		
                              self.log(Level.INFO, "Attributes Creation Error, " + resultSet2.getString("name") + " ==> ")
                      else:
                          try:
                              attID_ex1 = skCase.addArtifactAttributeType("TSK_" + resultSet2.getString("name").upper(), BlackboardAttribute.TSK_BLACKBOARD_ATTRIBUTE_VALUE_TYPE.LONG, resultSet2.getString("name"))
                              #self.log(Level.INFO, "attribure id for " + "TSK_" + resultSet2.getString("name") + " == " + str(attID_ex1))
                          except:		
                              self.log(Level.INFO, "Attributes Creation Error, " + resultSet2.getString("name") + " ==> ")
										 
                   resultSet3 = stmt.executeQuery(SQL_String_1)
                   while resultSet3.next():
                      art = file.newArtifact(artID_sam)
                      Column_Number = 1
                      for col_name in Column_Names:
                         #self.log(Level.INFO, "Result get information for column " + Column_Names[Column_Number - 1] + " ")
                         #self.log(Level.INFO, "Result get information for column " + Column_Types[Column_Number - 1] + " ")
                         #self.log(Level.INFO, "Result get information for column_number " + str(Column_Number) + " ")
                         c_name = "TSK_" + col_name
                         #self.log(Level.INFO, "Attribute Name is " + c_name + " Atribute Type is " + str(Column_Types[Column_Number - 1]))
                         attID_ex1 = skCase.getAttributeType(c_name)
                         if Column_Types[Column_Number - 1] == "text":
                             art.addAttribute(BlackboardAttribute(attID_ex1, ParseSAMIngestModuleFactory.moduleName, resultSet3.getString(Column_Number)))
                         else:
                             art.addAttribute(BlackboardAttribute(attID_ex1, ParseSAMIngestModuleFactory.moduleName, resultSet3.getInt(Column_Number)))
                         Column_Number = Column_Number + 1
						
               except SQLException as e:
                   self.log(Level.INFO, "Error getting values from contacts table (" + e.getMessage() + ")")

        # Clean up
           stmt.close()
           dbConn.close()

        try:
            os.remove(sam_database)
        except:
            self.log(Level.INFO, "Removal of file referenced by lclDbPath not successful")
			
		#Clean up EventLog directory and files
        for file in files:
            try:
                os.remove(sam_file)
            except:
                self.log(Level.INFO, "removal of SAM file failed " + sam_file)
        try:
            os.rmdir(temp_dir)		
        except:
            self.log(Level.INFO, "removal of SAM directory failed " + temp_dir)

        # Fire an event to notify the UI and others that there are new artifacts  
        IngestServices.getInstance().fireModuleDataEvent(
            ModuleDataEvent(ParseSAMIngestModuleFactory.moduleName, artID_sam_evt, None))
                
        # After all databases, post a message to the ingest messages in box.
        message = IngestMessage.createMessage(IngestMessage.MessageType.DATA,
            "SAM Parser", " SAM Has Been Analyzed " )
        IngestServices.getInstance().postMessage(message)

        # Fire an event to notify the UI and others that there are new artifacts  
        IngestServices.getInstance().fireModuleDataEvent(
            ModuleDataEvent(ParseSAMIngestModuleFactory.moduleName, artID_sam_evt, None))
        
        return IngestModule.ProcessResult.OK                
		
