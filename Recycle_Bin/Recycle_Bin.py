# This python autopsy module will Extract the Recycle bin and parse the $I file and bring the data
#  into Autopsy
#
# Contact: Mark McKinnon [Mark [dot] McKinnon <at> gmail [dot] com]
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

# Recycle_Bin.py.
# May 2019
# 
# Comments 
#   Version 1.0 - Initial version - May 2019
#   Version 1.1 - Remove external program dependecy and use rejistry 
#   Version 1.2 - Fix user not found in SAM
# 

import os
import shutil
import struct
import binascii
import codecs
import inspect

from com.williballenthin.rejistry import RegistryHiveFile
from com.williballenthin.rejistry import RegistryKey
from com.williballenthin.rejistry import RegistryParseException
from com.williballenthin.rejistry import RegistryValue
from java.lang import Class
from java.lang import System
from java.util.logging import Level
from java.io import File
from org.sleuthkit.datamodel import SleuthkitCase
from org.sleuthkit.datamodel import AbstractFile
from org.sleuthkit.datamodel import ReadContentInputStream
from org.sleuthkit.datamodel import BlackboardArtifact
from org.sleuthkit.datamodel import BlackboardAttribute
from org.sleuthkit.datamodel import TskData
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
from org.sleuthkit.autopsy.casemodule import Case
from org.sleuthkit.autopsy.casemodule.services import Services
from org.sleuthkit.autopsy.casemodule.services import FileManager
from org.sleuthkit.autopsy.datamodel import ContentUtils


# Factory that defines the name and details of the module and allows Autopsy
# to create instances of the modules that will do the analysis.
class RecBin2IngestModuleFactory(IngestModuleFactoryAdapter):

    def __init__(self):
        self.settings = None

    moduleName = "Recycle Bin Module"
    
    def getModuleDisplayName(self):
        return self.moduleName
    
    def getModuleDescription(self):
        return "Parse Recycle Bin Information for Vista and beyond"
    
    def getModuleVersionNumber(self):
        return "1.2"
    
    def hasIngestJobSettingsPanel(self):
        return False

    def isDataSourceIngestModuleFactory(self):
        return True

    def createDataSourceIngestModule(self, ingestOptions):
        return RecBin2IngestModule(self.settings)

# Data Source-level ingest module.  One gets created per data source.
class RecBin2IngestModule(DataSourceIngestModule):

    _logger = Logger.getLogger(RecBin2IngestModuleFactory.moduleName)

    def log(self, level, msg):
        self._logger.logp(level, self.__class__.__name__, inspect.stack()[1][3], msg)

    def __init__(self, settings):
        self.context = None
 
    # Where any setup and configuration is done
    def startUp(self, context):
        self.context = context
        self.registryKeyToFind = 'SAM/Domains/Account/Users'
        
    # Where the analysis is done.
    def process(self, dataSource, progressBar):

        # we don't know how much work there is yet
        progressBar.switchToIndeterminate()
        
        skCase = Case.getCurrentCase().getSleuthkitCase();
        fileManager = Case.getCurrentCase().getServices().getFileManager()

        # Create Recyclebin directory in temp directory, if it exists then continue on processing		
        Temp_Dir = Case.getCurrentCase().getTempDirectory()
        temp_dir = os.path.join(Temp_Dir, "recyclebin")
        self.log(Level.INFO, "create Directory " + temp_dir)
        try:
		    os.mkdir(temp_dir)
        except:
		    self.log(Level.INFO, "recyclebin Directory already exists " + temp_dir)

        systemAbsFile = []
        files = fileManager.findFiles(dataSource, "SAM", "Windows/System32/Config")
        numFiles = len(files)
        self.log(Level.INFO, "Number of SAM Files found ==> " + str(numFiles))
        
        userRids = {}
        
        for file in files:
        
            # Check if the user pressed cancel while we were busy
            if self.context.isJobCancelled():
                return IngestModule.ProcessResult.OK

            
            #self.log(Level.INFO, "Parent Path ==> " + str(file.getParentPath()))
            if file.getParentPath() == '/Windows/System32/Config/':    
                # Save the DB locally in the temp folder. use file id as name to reduce collisions
                lclDbPath = os.path.join(temp_dir, file.getName())
                ContentUtils.writeToFile(file, File(lclDbPath))
                # Process the SAM Registry File getting the Username and RID
                userRids = self.processSAMFile(lclDbPath)
            else:
                self.log(Level.INFO, "Skipping File " + file.getName() + " In Path " + file.getParentPath())

        # Setup Artifact and Attributes
        try:
            self.log(Level.INFO, "Begin Create New Artifacts")
            artID_ls = skCase.addArtifactType( "TSK_RECYCLE_BIN", "Recycle Bin")
        except:		
            self.log(Level.INFO, "Artifacts Creation Error, some artifacts may not exist now. ==> ")
        
        try:
            attIdFilePath = skCase.addArtifactAttributeType("TSK_FILE_NAME_PATH", BlackboardAttribute.TSK_BLACKBOARD_ATTRIBUTE_VALUE_TYPE.STRING, "Path File Name")
        except:
            attIdFilePath = skCase.getAttributeType("TSK_FILE_NAME_PATH")		
            self.log(Level.INFO, "Attributes Creation Error, TSK_FILE_NAME_PATH ==> ")
        
        try:
            attIdDelTime = skCase.addArtifactAttributeType("TSK_FILE_DEL_TIME", BlackboardAttribute.TSK_BLACKBOARD_ATTRIBUTE_VALUE_TYPE.DATETIME, "File Deletion Time")
        except:
            attIdDelTime = skCase.getAttributeType("TSK_FILE_DEL_TIME")		
            self.log(Level.INFO, "Attributes Creation Error, TSK_FILE_DEL_TIME ==> ")

        artifactName = "TSK_RECYCLE_BIN"
        artId = skCase.getArtifactTypeID(artifactName)
        attIdUserName = skCase.getAttributeType("TSK_USER_NAME")

        iFiles = fileManager.findFiles(dataSource, "$I%")
        numFiles = len(files)
        self.log(Level.INFO, "Number of $I Files found ==> " + str(numFiles))
            
        for iFile in iFiles:
            
            # Check if the user pressed cancel while we were busy
            if self.context.isJobCancelled():
                return IngestModule.ProcessResult.OK

            # Save the $I locally in the temp folder. use file id as name to reduce collisions
            lclDbPath = os.path.join(temp_dir, iFile.getName())
            ContentUtils.writeToFile(iFile, File(lclDbPath))
            self.log(Level.INFO, "Getting File " + iFile.getName() + " In Path " + iFile.getParentPath())

            rFileName = iFile.getName().replace("$I", "$R")
            rFiles = fileManager.findFiles(dataSource, rFileName, iFile.getParentPath())
            numRFiles = len(files)
            self.log(Level.INFO, "Number of $R Files found ==> " + str(numRFiles))
            for rFile in rFiles:
                if (rFile.getParentPath() == iFile.getParentPath()):
                    fileNamePath, deletedTimeStamp = self.getFileMetadata(os.path.join(temp_dir, iFile.getName()))
                    if fileNamePath != None:
                        art = rFile.newArtifact(artId)
                        self.log(Level.INFO, "Parent Path ==> " + iFile.getParentPath())
                        startSearch = iFile.getParentPath().rfind("-")
                        userRid = iFile.getParentPath()[startSearch + 1:].replace('/','')
                        art.addAttribute(BlackboardAttribute(attIdUserName, RecBin2IngestModuleFactory.moduleName, userRids.get(userRid, "Not Found!")))
                        art.addAttribute(BlackboardAttribute(attIdFilePath, RecBin2IngestModuleFactory.moduleName, fileNamePath))
                        art.addAttribute(BlackboardAttribute(attIdDelTime, RecBin2IngestModuleFactory.moduleName, deletedTimeStamp))
        
		#Clean up recyclebin directory and files
        try:
             shutil.rmtree(temp_dir)		
        except:
		     self.log(Level.INFO, "removal of directory tree failed " + temp_dir)
        
        # After all databases, post a message to the ingest messages in box.
        message = IngestMessage.createMessage(IngestMessage.MessageType.DATA,
            "RecycleBin", " Recycle Bin Files Have Been Analyzed " )
        IngestServices.getInstance().postMessage(message)

        return IngestModule.ProcessResult.OK                

    def getFileMetadata(self, fileName):
    
        fileRecord = 0
    
        with open(fileName, "rb") as file: 
            fileRecord = file.read()

        fileHeader = int(str(struct.unpack_from('<q', fileRecord[0:])[0]))
        #print ("File Header > " + str(fileHeader))

        if (fileHeader == 2):
           fileSize = int(str(struct.unpack_from('<q', fileRecord[8:])[0]))
           deleteTimeStamp = int(str(struct.unpack_from('<q', fileRecord[16:])[0])[0:11]) - 11644473600
           fileNameLength = int(str(struct.unpack_from('<l', fileRecord[24:])[0]))
           nameLength = "<" + str(fileNameLength * 2) + "s"
           fileName = struct.unpack_from(nameLength, fileRecord[28:])[0]
           fileNamePath = self.utf16decode(fileName)
           return fileNamePath, deleteTimeStamp
        else:
           fileSize = int(str(struct.unpack_from('<q', fileRecord[8:])[0]))
           deleteTimeStamp = int(str(struct.unpack_from('<q', fileRecord[16:])[0])[0:11]) - 11644473600
           fileName = str(struct.unpack_from('<520s', fileRecord[24:])[0]) 
           fileNamePath = self.utf16decode(fileName)
           return fileNamePath, deleteTimeStamp

        return None, None
        
    def processSAMFile(self, samHive):
    
        userId = {}
        samRegFile = RegistryHiveFile(File(samHive))
        currentKey = self.findRegistryKey(samRegFile, self.registryKeyToFind)
        samKey = currentKey.getSubkeyList()   
        for sk in samKey:
            registryKey = sk.getName()
            skValues = sk.getValueList()
            if len(skValues) > 0:
                for skVal in skValues:
                    if skVal.getName() == 'V':
                        value = skVal.getValue()
                        binData = value.getAsRawData()
                        hexArray = ""
                        arrayLength = binData.remaining()
                        for x in range(0, arrayLength):
                            binByte = binData.get()
                            # Have to check if this is a negative number or not.  Byte will be returned -127 to 127 instead of 0 to 255
                            if binByte < 0:
                                binByte = 256 + binByte
                            hexArray = hexArray + chr(binByte)
                        pos1 = int(str(struct.unpack_from('<l', hexArray[4:])[0]))
                        pos3 = int(str(struct.unpack_from('<l', hexArray[12:])[0])) + 204 
                        pos4 = int(str(struct.unpack_from('<l', hexArray[16:])[0]))
                        pos6 = int(str(struct.unpack_from('<l', hexArray[24:])[0])) + 204
                        pos7 = int(str(struct.unpack_from('<l', hexArray[28:])[0]))
                        pos9 = int(str(struct.unpack_from('<l', hexArray[36:])[0])) + 204
                        pos10 = int(str(struct.unpack_from('<l', hexArray[40:])[0]))
                        fmtStringName = "<" + str(pos4) + "s"		  
                        fmtStringFullname = ">" + str(pos7) + "s"
                        fmtStringComment = ">" + str(pos10) + "s"
                        userName = struct.unpack_from(fmtStringName, hexArray[pos3:])[0]
                        fullName = struct.unpack_from(fmtStringFullname, hexArray[pos6:])[0]
                        comment = struct.unpack_from(fmtStringComment, hexArray[pos9:])[0]
                        userName = self.utf16decode(userName)
                        userId[str(int(registryKey, 16))] = userName

        return userId

    def findRegistryKey(self, registryHiveFile, registryKey):
    
        rootKey = registryHiveFile.getRoot()
        regKeyList = registryKey.split('/')
        currentKey = rootKey
        for key in regKeyList:
            self.log(Level.INFO, "Key value is ==> " + key)
            self.log(Level.INFO, "Current Key is ==> " + str(currentKey))
            currentKey = currentKey.getSubkey(key) 
        return currentKey   

    def utf16decode(self, bytes):

        ## Take the UTF-16LE encoded strings as bytes and convert to a UTF-8 string. Jython-compatible.
        ## code taken from Sam Koffman that he created for his plugin Autopsy-MSOT
        ## https://github.com/MadScientistAssociation/Autopsy-MSOT/blob/5f31ce521f4df3839fc825d00e82d9a6e97dfcff/lib/misc_functions_aut.py

        bytes = binascii.hexlify(bytes)
        bytes = [bytes[i:i+2] for i in range(0, len(bytes), 2)]
        bytes = (''.join(filter(lambda a: a !='00', bytes)))
        bytes = codecs.decode(bytes, 'hex')
        return(bytes)
        
