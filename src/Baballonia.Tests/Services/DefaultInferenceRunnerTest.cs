using System;
using Baballonia.Services;
using Baballonia.Services.Inference;
using JetBrains.Annotations;
using Microsoft.Extensions.Logging;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using OpenCvSharp;
using LogLevel = Microsoft.Extensions.Logging.LogLevel;

namespace Baballonia.Tests.Services;

[TestClass]
[TestSubject(typeof(DefaultInferenceRunner))]
public class DefaultInferenceRunnerTest
{
    private DefaultInferenceRunner _defaultInference;

    [TestInitialize]
    public void Initialize()
    {
        var loggerFactory = LoggerFactory.Create(builder =>
        {
            builder.AddConsole(); // Or AddDebug()
            builder.SetMinimumLevel(LogLevel.Debug);
        });

        try
        {
            _defaultInference = new DefaultInferenceRunner(loggerFactory);
            _defaultInference.Setup("faceModel.onnx", false);
        }
        catch (Exception ex)
        {
            Assert.Fail("Expected no exception, but got: " + ex);
        }
    }

    [TestMethod]
    public void TestUseGpuNotThrows()
    {
        try
        {
            _defaultInference.Setup("faceModel.onnx", true);
        }
        catch (Exception ex)
        {
            Assert.Fail("Expected no exception, but got: " + ex);
        }
    }

    [TestMethod]
    [DataRow("faceModel.onnx", false)]
    [DataRow("faceModel.onnx", true)]
    [DataRow("eyeModel.onnx", false)]
    [DataRow("eyeModel.onnx", true)]
    public void TestInferenceWithGpuFallback(string modelPath, bool useGpu)
    {
        _defaultInference.Setup(modelPath, useGpu);
        _defaultInference.GetInputTensor().Buffer.Span.Fill(0.5f);

        var result = _defaultInference.Run();

        Assert.IsNotNull(result);
        Assert.IsTrue(result.Length > 0);
        Assert.AreEqual(result.Length, _defaultInference.OutputSize);
        foreach (var value in result)
            Assert.IsTrue(float.IsFinite(value));
    }

    [TestMethod]
    public void TestSinglePass()
    {
        var image = new Mat(_defaultInference.InputSize.Height, _defaultInference.InputSize.Width,
            MatType.CV_32F, new Scalar(0.5f));
        var conv = new MatToFloatTensorConverter();
        conv.Convert(image, _defaultInference.InputTensor);
        var result = _defaultInference.Run();
        Assert.IsNotNull(result);
    }

    [TestMethod]
    public void TestMultiPass()
    {
        for (var i = 0; i < 10; i++)
        {
            var image = new Mat(224, 224, MatType.CV_32F, new Scalar(0.5f));
            var conv = new MatToFloatTensorConverter();
            conv.Convert(image, _defaultInference.InputTensor);
            var result = _defaultInference.Run();
            Assert.IsNotNull(result);
        }
    }
}
